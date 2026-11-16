"""端点接入清单业务规则。

批量统一准入的口径全部收敛在这一层，路由层不做业务判断：

1. 接口地址先规范化（scheme/主机大小写、默认端口、尾斜杠、query 顺序、fragment），
   规范化后同一「端点编号 + 规范化接口地址」只能有一条准入记录，重复项单独隔离退回；
2. 认证模板/认证类型/认证请求头/认证密钥四件套缺一不可，认证信息不全的记录单独退回，
   不允许混进待采集清单；
3. 同一迁移批次内出现多套完整认证模板（旧模板仍在使用又收到新的接入声明）视为冲突，
   冲突项保持「待裁决」，由运营人员裁决；一次采集请求始终只落一套完整认证参数；
4. 准入回执与隔离原因同表可见；准入结果回写接口管理清单待办区，失败项驱动端点详情标记。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.store import store

COLLECT_MODULE = "collect_endpoint"
ADMISSION_MODULE = "admission"
ENDPOINT_MODULE = "endpoint"

# 认证四件套：缺任意一项都不能接入，执行端会拿不到请求头
AUTH_FIELDS = ["认证模板", "认证类型", "认证请求头", "认证密钥"]

# 准入状态机：待裁决 -> 已接入/已隔离；已接入与已隔离都是终态
STATUS_ADMITTED = "已接入"
STATUS_QUARANTINED = "已隔离"
STATUS_PENDING = "待裁决"

SOURCE_DONE = "已处理"
SOURCE_SKIPPED = "已跳过"

DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(raw: str) -> str:
    """规范化接口地址。

    处理：首尾空白、主机小写、去默认端口、折叠重复斜杠、去尾斜杠、query 排序、丢 fragment。
    无法解析或缺少 scheme/host 时抛 ValueError，由调用方记为隔离原因。
    """
    text = (raw or "").strip()
    if not text or "://" not in text:
        raise ValueError("接口地址缺少协议头或为空")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    host = parts.hostname
    if not host:
        raise ValueError("接口地址缺少主机名")
    if any(ch.isspace() for ch in host) or " " in text.split("?", 1)[0]:
        raise ValueError("接口地址包含非法空白字符")
    port = parts.port  # 非法端口会抛 ValueError，交给调用方统一记隔离原因
    netloc = host
    if port is not None and DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"
    path = parts.path or ""
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = "&".join(sorted(q for q in parts.query.split("&") if q))
    return urlunsplit((scheme, netloc, path, query, ""))


class AdmissionService:
    # ---------- 采集端点配置（输入侧） ----------
    def list_collect_endpoints(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(COLLECT_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("端点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("处理状态") == status]
        if batch:
            rows = [row for row in rows if row.get("迁移批次") == batch]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_collect_endpoint(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(COLLECT_MODULE, entry_id)

    # ---------- 端点接入清单（结果侧） ----------
    def list_admissions(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(ADMISSION_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("端点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("准入状态") == status]
        if batch:
            rows = [row for row in rows if row.get("来源批次") == batch]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_admission(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(ADMISSION_MODULE, entry_id)

    # ---------- 批量准入 ----------
    def batch_admit(self, source_ids: list[int]) -> dict[str, Any]:
        """对多选的采集端点配置执行统一准入，生成端点接入清单数据集。"""
        if not source_ids:
            return {
                "ok": False,
                "message": "未选择任何采集端点，无法生成接入清单",
                "processed": [],
                "summary": {"admitted": 0, "quarantined": 0, "conflict": 0, "skipped": 0},
            }

        processed: list[dict[str, Any]] = []
        counters = {"admitted": 0, "quarantined": 0, "conflict": 0, "skipped": 0}
        seq = 0

        for source_id in source_ids:
            source = store.find(COLLECT_MODULE, int(source_id))
            if source is None:
                counters["skipped"] += 1
                processed.append({"采集配置ID": source_id, "准入状态": "已跳过",
                                  "隔离原因": f"采集端点配置 {source_id} 不存在"})
                continue
            if source.get("处理状态") != "未处理":
                counters["skipped"] += 1
                processed.append({"采集配置ID": source_id, "端点编号": source.get("端点编号"),
                                  "准入状态": "已跳过",
                                  "隔离原因": f"该采集配置已处于「{source.get('处理状态')}」，不重复准入"})
                continue

            seq += 1
            record = self._build_admission(source, seq)
            store.rows(ADMISSION_MODULE).append(record)

            status = record["准入状态"]
            if status == STATUS_ADMITTED:
                counters["admitted"] += 1
                source["处理状态"] = SOURCE_DONE
                source["pending"] = False
                source["abnormal"] = False
            elif status == STATUS_PENDING:
                # 待裁决项对运营人员仍是待处理输入，保留在待办里
                counters["conflict"] += 1
                source["处理状态"] = SOURCE_DONE
                source["pending"] = True
                source["abnormal"] = True
            else:
                counters["quarantined"] += 1
                source["处理状态"] = SOURCE_SKIPPED
                source["pending"] = True
                source["abnormal"] = True
            self._write_back_endpoint(record)
            processed.append({key: record.get(key) for key in
                              ("id", "端点编号", "准入状态", "隔离原因", "准入回执", "采集配置ID")})

        message = (
            f"批量准入完成：接入 {counters['admitted']} 条，"
            f"隔离退回 {counters['quarantined']} 条，"
            f"冲突待裁决 {counters['conflict']} 条，跳过 {counters['skipped']} 条"
        )
        return {"ok": True, "message": message, "processed": processed, "summary": counters}

    def _build_admission(self, source: dict[str, Any], seq: int) -> dict[str, Any]:
        rows = store.rows(ADMISSION_MODULE)
        record: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "采集配置ID": source["id"],
            "端点编号": str(source.get("端点编号") or "").strip(),
            "接口地址": source.get("接口地址", ""),
            "规范化接口地址": "",
            "请求方法": source.get("请求方法", ""),
            "迁移批次": source.get("迁移批次", ""),
            "来源批次": source.get("迁移批次", ""),
            "认证模板": source.get("认证模板", ""),
            "认证类型": source.get("认证类型", ""),
            "认证请求头": source.get("认证请求头", ""),
            "认证密钥": source.get("认证密钥", ""),
            "准入状态": STATUS_QUARANTINED,
            "隔离原因": "",
            "准入回执": "",
            "处理时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pending": False,
            "abnormal": False,
            "候选认证模板": [],
        }

        # 规则一：接口地址规范化；地址非法直接退回，后续校验不再继续
        try:
            normalized = normalize_url(str(source.get("接口地址") or ""))
        except ValueError as exc:
            record["隔离原因"] = f"接口地址不合法：{exc}"
            record["abnormal"] = True
            return record
        record["规范化接口地址"] = normalized

        # 规则二：认证四件套完整性，缺失项单独退回，不进入待采集清单
        missing = [field for field in AUTH_FIELDS if not str(source.get(field) or "").strip()]
        if missing:
            record["隔离原因"] = f"认证信息不全，缺少：{'、'.join(missing)}"
            record["abnormal"] = True
            return record

        # 规则三：同一端点编号 + 规范化接口地址只能对应一条非隔离的准入记录
        duplicate = next(
            (row for row in rows
             if row.get("端点编号") == record["端点编号"]
             and row.get("规范化接口地址") == normalized
             and row.get("准入状态") != STATUS_QUARANTINED),
            None,
        )
        if duplicate is not None:
            record["隔离原因"] = (
                f"重复准入：端点编号与规范化接口地址已存在准入记录 #{duplicate['id']}"
                f"（回执 {duplicate.get('准入回执') or '待裁决'}），一套地址只允许一条准入记录"
            )
            record["abnormal"] = True
            return record

        # 规则四：同一迁移批次内出现多套认证模板 -> 旧模板与新接入声明冲突，转人工裁决
        conflict = next(
            (row for row in rows
             if row.get("迁移批次") == record["迁移批次"]
             and row.get("准入状态") in (STATUS_ADMITTED, STATUS_PENDING)
             and row.get("认证模板") != record["认证模板"]),
            None,
        )
        if conflict is not None:
            record["准入状态"] = STATUS_PENDING
            record["隔离原因"] = (
                f"认证模板冲突：迁移批次 {record['迁移批次']} 已使用模板"
                f"「{conflict.get('认证模板')}」，本次接入声明模板「{record['认证模板']}」，需裁决取舍"
            )
            record["候选认证模板"] = sorted({
                str(conflict.get("认证模板")), record["认证模板"],
            })
            record["pending"] = True
            record["abnormal"] = True
            return record

        # 通过全部校验：落一套完整认证参数并生成准入回执
        record["准入状态"] = STATUS_ADMITTED
        record["准入回执"] = self._make_receipt(record, seq)
        return record

    # ---------- 冲突裁决 ----------
    def resolve_conflict(
        self,
        admission_id: int,
        decision: str,
        chosen_template: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        record = store.find(ADMISSION_MODULE, admission_id)
        if record is None:
            return None, f"准入记录 {admission_id} 不存在"
        if record.get("准入状态") != STATUS_PENDING:
            return None, f"准入记录 {admission_id} 当前为「{record.get('准入状态')}」，无需裁决"

        if decision == "reject":
            record["准入状态"] = STATUS_QUARANTINED
            record["隔离原因"] = f"裁决退回：{record.get('隔离原因', '认证模板冲突未被采纳')}"
            record["pending"] = False
            record["abnormal"] = True
            source = store.find(COLLECT_MODULE, int(record["采集配置ID"]))
            if source is not None:
                source["处理状态"] = SOURCE_SKIPPED
            self._write_back_endpoint(record)
            return record, "冲突项已裁决退回并隔离"

        if decision != "adopt":
            return None, "裁决动作只支持 adopt（采纳模板接入）或 reject（退回隔离）"

        chosen = str(chosen_template or record.get("认证模板") or "").strip()
        candidates = record.get("候选认证模板") or [record.get("认证模板")]
        if chosen not in candidates:
            return None, f"候选模板中没有「{chosen}」，无法采纳"

        if chosen != record.get("认证模板"):
            # 采纳的是对方模板：本记录没有该模板的完整参数，不能凭空接入
            record["准入状态"] = STATUS_QUARANTINED
            record["隔离原因"] = (
                f"裁决退回：采纳模板「{chosen}」但本采集请求缺少该模板的完整认证参数，"
                "一次采集请求只能关联自身携带的一套完整认证参数"
            )
            record["pending"] = False
            record["abnormal"] = True
            source = store.find(COLLECT_MODULE, int(record["采集配置ID"]))
            if source is not None:
                source["处理状态"] = SOURCE_SKIPPED
            self._write_back_endpoint(record)
            return record, "已按裁决隔离：采纳的模板缺少配套认证参数"

        record["准入状态"] = STATUS_ADMITTED
        record["隔离原因"] = ""
        record["准入回执"] = self._make_receipt(record, admission_id)
        record["pending"] = False
        record["abnormal"] = False
        source = store.find(COLLECT_MODULE, int(record["采集配置ID"]))
        if source is not None:
            source["处理状态"] = SOURCE_DONE
            source["pending"] = False
            source["abnormal"] = False
        self._write_back_endpoint(record)
        return record, f"已采纳模板「{chosen}」完成接入，回执 {record['准入回执']}"

    # ---------- 回写接口管理清单 ----------
    def _write_back_endpoint(self, record: dict[str, Any]) -> None:
        """准入结果回写接口管理清单待办区；失败项在端点详情打上认证失败标记。"""
        rows = store.rows(ENDPOINT_MODULE)
        target = next(
            (row for row in rows if row.get("端点编号") == record["端点编号"]),
            None,
        )
        if target is None:
            target = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "端点编号": record["端点编号"],
                "接口地址": record["接口地址"],
                "请求方法": record["请求方法"],
                "认证方式": record["认证类型"],
                "请求头配置": record["认证请求头"],
                "参数模板": "",
                "响应格式": "",
                "超时秒数": "",
            }
            rows.append(target)

        status = record["准入状态"]
        # 已接入状态具粘性：同一端点后续的重复退回/冲突不能覆盖成功状态，
        # 否则页面会把实际已接入、执行端已拿到请求头的端点误标成认证失败。
        if target.get("准入回执") and status != STATUS_ADMITTED:
            target["最近退回原因"] = record.get("隔离原因", "")
            target["最近处理时间"] = record.get("处理时间", "")
            return

        target["规范化接口地址"] = record.get("规范化接口地址", "")
        target["认证模板"] = record.get("认证模板", "")
        target["准入回执"] = record.get("准入回执", "")
        target["隔离原因"] = record.get("隔离原因", "")
        target["最近处理时间"] = record.get("处理时间", "")
        target["来源迁移批次"] = record.get("迁移批次", "")

        if status == STATUS_ADMITTED:
            # 只落一套完整认证参数，保证执行端拿得到请求头
            target["认证方式"] = record["认证类型"]
            target["请求头配置"] = record["认证请求头"]
            target["认证参数快照"] = self._auth_snapshot(record)
            target["认证失败标记"] = False
            target["失败原因"] = ""
            target["status"] = "待校验"
            target["pending"] = True
            target["abnormal"] = False
        elif status == STATUS_PENDING:
            target["认证失败标记"] = False
            target["失败原因"] = ""
            target["待裁决冲突"] = record.get("隔离原因", "")
            target["status"] = "待校验"
            target["pending"] = True
            target["abnormal"] = True
        else:
            # 隔离退回：页面不得显示已接入，端点详情必须带认证失败/隔离标记
            target["认证失败标记"] = True
            target["失败原因"] = record.get("隔离原因", "")
            target["status"] = "认证失败"
            target["pending"] = True
            target["abnormal"] = True

    @staticmethod
    def _auth_snapshot(record: dict[str, Any]) -> dict[str, str]:
        """一次采集请求只关联一套完整认证参数的落库快照。"""
        return {field: str(record.get(field, "")) for field in AUTH_FIELDS}

    @staticmethod
    def _make_receipt(record: dict[str, Any], seq: int) -> str:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"RCV-{stamp}-{int(seq):04d}"
