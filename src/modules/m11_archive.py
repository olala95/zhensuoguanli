# -*- coding: utf-8 -*-
"""M11 病历归档（F76-F83）。

表所有权：record_version / record_amendment / record_closure / record_archive /
         record_hash / record_seal / record_unseal / record_export
状态机：DRAFT→CONFIRMED（M03 F19，同时建 v1）→ARCHIVED（F79）→SEALED（F81）→UNSEALED（F82）
异常码：E3090 Hash 链校验失败 / E3091 重复解封 / E3092 导出路径非法
边界：
  · **本模块不写 M03 的任何表**：内容读取走 m03_record.build_snapshot()，
    内容写入走 m03_record.apply_amendment()，状态推进走 m03_record.transition()（R2/R5）
  · 版本与 Hash 只增不改（数据库触发器兜底），篡改只能被发现、不能被修补（R-M11-2）
  · 导出只记事件，不落内容副本（R-M11-8）
"""

import hashlib
import json
import os
import re

from core import db, audit, config, timeutil as tu, ids
from core import runtime
from core.api import route
from core.errors import (require, AppError, conflict, not_found,
                         DATA_REQUIRED, BIZ_STATE_INVALID)
from modules import m03_record

MODULE = "M11"

HASH_CHAIN_BROKEN = "E3090"
ALREADY_UNSEALED = "E3091"
EXPORT_PATH_INVALID = "E3092"

ALGORITHM = "SHA256"
EXPORT_FORMATS = ("PDF", "HTML", "JSON")
MIN_REASON_LEN = 5

SOURCE_CONFIRM = "CONFIRM"
SOURCE_AMEND = "AMEND"


# ---------------- 基础工具 ----------------

def _canonical(obj):
    """规范序列化：键排序 + 紧凑分隔 + UTF-8，保证同一内容必得同一 Hash（F80 R2）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _row(record_id):
    row = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (record_id,))
    if row is None:
        raise not_found("病历不存在")
    return row


def _max_version(record_id):
    row = db.query_one("SELECT MAX(version_no) m FROM record_version WHERE record_id=?",
                       (record_id,))
    return row["m"] or 0


def _idem(table, key):
    if not key:
        return None
    return db.query_one("SELECT * FROM %s WHERE idempotent_key=?" % table, (key,))


def _closed(record_id):
    return db.query_one("SELECT * FROM record_closure WHERE record_id=?", (record_id,))


def _sealed(record_id):
    return db.query_one("SELECT * FROM record_seal WHERE record_id=? ORDER BY id DESC LIMIT 1",
                        (record_id,))


# ---------------- F76 版本 + F80 Hash 链 ----------------

def create_version(conn, record_id, source=SOURCE_CONFIRM, change_note=None, ctx=None,
                   snapshot=None):
    """建一个版本并续写 Hash 链。**必须在事务内调用**（版本与 Hash 同生共死）。

    hash = sha256(canonical(snapshot) + prev_hash)；链首 prev_hash 为 ""。
    """
    ctx = ctx or audit.get_ctx()
    snap = snapshot if snapshot is not None else m03_record.build_snapshot(record_id)
    snap_text = _canonical(snap)
    prev = db.query_one("SELECT hash FROM record_hash WHERE record_id=? "
                        "ORDER BY version_no DESC LIMIT 1", (record_id,))
    prev_hash = prev["hash"] if prev else ""
    version_no = _max_version(record_id) + 1
    now = tu.now_str()

    db.execute(conn, """
        INSERT INTO record_version (record_id, version_no, snapshot_json, source, change_note,
                                    created_at, created_by, created_role)
        VALUES (?,?,?,?,?,?,?,?)
    """, (record_id, version_no, snap_text, source, change_note, now, ctx.user_id, ctx.role_code))
    digest = _sha(snap_text + prev_hash)
    db.execute(conn, """
        INSERT INTO record_hash (record_id, version_no, algorithm, prev_hash, hash,
                                 created_at, created_by)
        VALUES (?,?,?,?,?,?,?)
    """, (record_id, version_no, ALGORITHM, prev_hash, digest, now, ctx.user_id))
    return {"record_id": record_id, "version_no": version_no, "prev_hash": prev_hash,
            "hash": digest}


def verify_hash(record_id):
    """F80 R4 校验：从链首逐条重算。返回 (ok, first_bad_version, reason)。"""
    vers = db.query("SELECT version_no, snapshot_json FROM record_version "
                    "WHERE record_id=? ORDER BY version_no", (record_id,))
    hashes = db.query("SELECT version_no, prev_hash, hash FROM record_hash "
                      "WHERE record_id=? ORDER BY version_no", (record_id,))
    if not vers or not hashes:
        return False, None, "无版本或 Hash 记录"
    if len(vers) != len(hashes):
        return False, None, "版本数(%d)与 Hash 数(%d)不一致" % (len(vers), len(hashes))
    prev = ""
    for v, h in zip(vers, hashes):
        if v["version_no"] != h["version_no"]:
            return False, v["version_no"], "版本号与 Hash 记录不对应"
        if (h["prev_hash"] or "") != prev:
            return False, v["version_no"], "prev_hash 与链前值不符（链被剪断或重排）"
        try:
            snap = json.loads(v["snapshot_json"])
        except ValueError:
            return False, v["version_no"], "快照 JSON 无法解析（内容被外部改动）"
        expected = _sha(_canonical(snap) + prev)
        if (h["hash"] or "") != expected:
            return False, v["version_no"], "快照内容与 Hash 不符（内容被篡改）"
        prev = h["hash"]
    return True, None, ""


def _require_chain_ok(record_id, action):
    """F80 R6：归档/封存/导出前强制校验。失败 → 拒绝 + 高风险审计。

    **必须在主事务之外调用**：篡改证据要先提交落库，否则主事务回滚会把审计一起带走，
    等于"篡改被发现又被抹掉"。
    """
    ok, bad, why = verify_hash(record_id)
    if ok:
        return True
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.%s" % action, "record", record_id,
                              result="FAIL", error_code=HASH_CHAIN_BROKEN,
                              detail={"version_no": bad, "reason": why})
        audit.audit_high_risk(conn, "HASH_BROKEN", "record", record_id,
                              "执行 %s 前 Hash 链校验失败：%s（版本 %s）" % (action, why, bad),
                              {"action": action, "version_no": bad, "reason": why})
    raise AppError(HASH_CHAIN_BROKEN,
                   "病历 Hash 链校验失败，已拒绝%s：%s（首个异常版本 %s）"
                   % (action, why, bad), retryable=False, http_status=409,
                   detail={"version_no": bad, "reason": why})


# ---------------- F78 关闭 ----------------

@route("/api/record/close", "POST", perm="record.close", module="M11", action="record.close")
def close(ctx, body, q):
    """F78 病历关闭：CONFIRMED 上打"不再接受修改"标记，不新增契约状态。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    rec = _row(rid)
    if rec["status"] != m03_record.CONFIRMED:
        raise conflict("只有 CONFIRMED 病历可以关闭（当前 %s）" % rec["status"],
                       code=BIZ_STATE_INVALID)
    key = body.get("idempotent_key")
    exist = _idem("record_closure", key)
    if exist:
        return dict(exist)
    if _closed(rid):
        raise conflict("病历已关闭，不可重复关闭", code=BIZ_STATE_INVALID)
    now = tu.now_str()
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.close", "record", rid,
                              detail={"summary": body.get("summary")})
        cur = db.execute(conn, """
            INSERT INTO record_closure (record_id, summary, closed_by, closed_role, closed_at,
                                        idempotent_key, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (rid, body.get("summary"), ctx.user_id, ctx.role_code, now, key, now))
    return {"record_id": rid, "closure_id": cur.lastrowid, "closed_at": now}


# ---------------- F77 修改留痕 ----------------

@route("/api/record/amend", "POST", perm="record.amend", module="M11", action="record.amend")
def amend(ctx, body, q):
    """F77 已确认病历的唯一修改路径：留痕 + 新版本 + 续 Hash。

    body: {record_id, reason, changes:[{field_path, after_value}], idempotent_key}
    """
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise AppError(DATA_REQUIRED, "修改原因必填（改了什么 + 为什么）")
    if len(reason) < MIN_REASON_LEN:
        raise AppError(DATA_REQUIRED, "修改原因过于简略（至少 %d 个字）" % MIN_REASON_LEN)
    changes = body.get("changes") or []
    require(changes, "缺少 changes（要改哪些字段）")
    rec = _row(rid)
    if rec["status"] not in (m03_record.CONFIRMED, m03_record.UNSEALED):
        raise conflict("只有 CONFIRMED（或解封后 UNSEALED）病历可以修改留痕（当前 %s，"
                       "已封存需先解封）" % rec["status"], code=BIZ_STATE_INVALID)
    # 关闭后禁止修改；但解封是管理员授权的高风险例外，允许更正（F82 R8）
    if rec["status"] == m03_record.CONFIRMED and _closed(rid):
        raise conflict("病历已关闭，禁止修改（关闭后不再接受修改）", code=BIZ_STATE_INVALID)

    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.amend", "record", rid,
                              detail={"reason": reason,
                                      "fields": [c.get("field_path") for c in changes]})
        # R3：先经 M03 接口落新内容（返回前后值），再建新版本（快照必须反映新内容，
        # 否则版本与内容不一致），最后写留痕。全事务，任一步失败整体回滚。
        applied = m03_record.apply_amendment(conn, rid, changes, ctx)
        version = create_version(conn, rid, source=SOURCE_AMEND, change_note=reason, ctx=ctx)
        now = tu.now_str()
        for item in applied:
            db.execute(conn, """
                INSERT INTO record_amendment (record_id, version_no, field_path, before_value,
                                              after_value, reason, operator_id, operator_role,
                                              created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (rid, version["version_no"], item["field_path"],
                  item["before_value"], item["after_value"], reason,
                  ctx.user_id, ctx.role_code, now))
    return {"record_id": rid, "version_no": version["version_no"], "hash": version["hash"],
            "amended": len(applied)}


# ---------------- F79 归档 ----------------

@route("/api/record/archive", "POST", perm="record.archive", module="M11",
       action="record.archive")
def archive(ctx, body, q):
    """F79 按月归档：AR{yyyyMM}{4位}，CONFIRMED → ARCHIVED（经 M03 状态机）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    key = body.get("idempotent_key")
    exist = _idem("record_archive", key)
    if exist:
        out = dict(exist)
        out["archive_id"] = exist["id"]
        out["duplicate"] = True
        return out
    rec = _row(rid)
    re_archive = rec["status"] == m03_record.UNSEALED
    if rec["status"] not in (m03_record.CONFIRMED, m03_record.UNSEALED):
        raise conflict("只有 CONFIRMED 病历可以归档（当前 %s）" % rec["status"],
                       code=BIZ_STATE_INVALID)
    if not re_archive and db.query_one("SELECT id FROM record_archive WHERE record_id=?", (rid,)):
        raise conflict("该病历已归档，不可重复归档（解封后需走重新归档）",
                       code=BIZ_STATE_INVALID)
    _require_chain_ok(rid, "归档")          # 链异常：独立事务先落审计，再拒绝
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.archive", "record", rid,
                              detail={"remark": body.get("remark")})
        month = tu.now().strftime("%Y%m")
        archive_no = ids.next_no(conn, "ARCHIVE")
        version_no = _max_version(rid)
        now = tu.now_str()
        cur = db.execute(conn, """
            INSERT INTO record_archive (archive_no, record_id, archive_month, version_no,
                                        closed_flag, hash_verified, archived_by, archived_role,
                                        archived_at, idempotent_key, remark, created_at)
            VALUES (?,?,?,?,?,1,?,?,?,?,?,?)
        """, (archive_no, rid, month, version_no, 1 if _closed(rid) else 0,
              ctx.user_id, ctx.role_code, now, key, body.get("remark"), now))
        m03_record.transition(conn, rid, m03_record.ARCHIVED, body.get("remark"), ctx)
    return {"record_id": rid, "archive_no": archive_no, "archive_month": month,
            "version_no": version_no, "archive_id": cur.lastrowid,
            "closed_flag": 1 if _closed(rid) else 0}


@route("/api/record/archive/list", "GET", perm="record.view", module="M11",
       action="record.archive.list")
def archive_list(ctx, body, q):
    """F79 R7 按月列出档案。"""
    month = q.get("month") or tu.now().strftime("%Y%m")
    sql = """SELECT a.*, r.patient_id, r.visit_id, p.name AS patient_name
             FROM record_archive a
             JOIN record r ON r.id=a.record_id
             LEFT JOIN patient p ON p.id=r.patient_id
             WHERE a.archive_month=?"""
    params = [month]
    if q.get("patient_id"):
        sql += " AND r.patient_id=?"
        params.append(q["patient_id"])
    sql += " ORDER BY a.archive_no"
    return {"month": month, "items": db.query(sql, tuple(params))}


# ---------------- F81 封存 / F82 解封 ----------------

@route("/api/record/seal", "POST", perm="record.seal", module="M11", action="record.seal")
def seal(ctx, body, q):
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    key = body.get("idempotent_key")
    exist = _idem("record_seal", key)
    if exist:
        return {"record_id": exist["record_id"], "seal_id": exist["id"],
                "sealed_at": exist["sealed_at"], "duplicate": True}
    rec = _row(rid)
    if rec["status"] != m03_record.ARCHIVED:
        raise conflict("只有 ARCHIVED 病历可以封存（当前 %s）" % rec["status"],
                       code=BIZ_STATE_INVALID)
    # 同一归档周期内不可重复封存；解封后重新归档（新周期）允许再次封存
    n_arch = db.query_one("SELECT COUNT(*) c FROM record_archive WHERE record_id=?",
                          (rid,))["c"]
    n_seal = db.query_one("SELECT COUNT(*) c FROM record_seal WHERE record_id=?", (rid,))["c"]
    if n_arch > 0 and n_seal >= n_arch:
        raise conflict("该归档周期已封存过，不能重复封存（需解封后重新归档）",
                       code=BIZ_STATE_INVALID)
    arc = db.query_one("SELECT archive_no FROM record_archive WHERE record_id=? "
                       "ORDER BY id DESC LIMIT 1", (rid,))
    _require_chain_ok(rid, "封存")          # 链异常：独立事务先落审计，再拒绝
    now = tu.now_str()
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.seal", "record", rid,
                              detail={"reason": body.get("reason")})
        cur = db.execute(conn, """
            INSERT INTO record_seal (record_id, archive_no, reason, sealed_by, sealed_role,
                                     sealed_at, idempotent_key, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (rid, arc["archive_no"] if arc else None, body.get("reason"),
              ctx.user_id, ctx.role_code, now, key, now))
        m03_record.transition(conn, rid, m03_record.SEALED, body.get("reason"), ctx)
    return {"record_id": rid, "seal_id": cur.lastrowid, "sealed_at": now}


@route("/api/record/unseal", "POST", perm="record.unseal", module="M11",
       action="record.unseal")
def unseal(ctx, body, q):
    """F82 解封：高风险，必填原因，写 audit_high_risk；封存记录永久保留。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise AppError(DATA_REQUIRED, "解封原因必填（属高风险操作，必须说明）")
    if len(reason) < MIN_REASON_LEN:
        raise AppError(DATA_REQUIRED, "解封原因过于简略（至少 %d 个字）" % MIN_REASON_LEN)
    key = body.get("idempotent_key")
    exist = _idem("record_unseal", key)
    if exist:
        return {"record_id": exist["record_id"], "unseal_id": exist["id"],
                "unsealed_at": exist["unsealed_at"], "reason": exist["reason"],
                "duplicate": True}
    rec = _row(rid)
    n_seal = db.query_one("SELECT COUNT(*) c FROM record_seal WHERE record_id=?", (rid,))["c"]
    n_unseal = db.query_one("SELECT COUNT(*) c FROM record_unseal WHERE record_id=?", (rid,))["c"]
    if n_seal == 0:
        raise conflict("该病历从未封存，不能解封", code=BIZ_STATE_INVALID)
    if n_unseal >= n_seal:
        raise AppError(ALREADY_UNSEALED,
                       "该封存周期已解封，不可重复解封（UNSEALED 不可原地再封存）",
                       retryable=False, http_status=409)
    if rec["status"] != m03_record.SEALED:
        raise conflict("只有 SEALED 病历可以解封（当前 %s）" % rec["status"],
                       code=BIZ_STATE_INVALID)
    arc = db.query_one("SELECT archive_no FROM record_archive WHERE record_id=? "
                       "ORDER BY id DESC LIMIT 1", (rid,))
    now = tu.now_str()
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.unseal", "record", rid,
                              detail={"reason": reason})
        cur = db.execute(conn, """
            INSERT INTO record_unseal (record_id, archive_no, reason, unsealed_by, unsealed_role,
                                       unsealed_at, idempotent_key, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (rid, arc["archive_no"] if arc else None, reason,
              ctx.user_id, ctx.role_code, now, key, now))
        audit.audit_high_risk(conn, audit.RISK_UNSEAL, "record", rid, reason,
                              {"archive_no": arc["archive_no"] if arc else None,
                               "unsealed_at": now})
        m03_record.transition(conn, rid, m03_record.UNSEALED, reason, ctx)
    return {"record_id": rid, "unseal_id": cur.lastrowid, "unsealed_at": now,
            "reason": reason}


# ---------------- F80 Hash 校验 ----------------

@route("/api/record/hash/verify", "GET", perm="record.view", module="M11",
       action="record.hash.verify")
def hash_verify(ctx, body, q):
    rid = q.get("record_id")
    require(rid, "缺少 record_id")
    _row(rid)
    ok, bad, why = verify_hash(rid)
    return {"record_id": rid, "valid": ok, "first_bad_version": bad, "reason": why,
            "version_count": _max_version(rid)}


@route("/api/record/hash/verify_all", "POST", perm="record.hash.verify", module="M11",
       action="record.hash.verify_all")
def hash_verify_all(ctx, body, q):
    """F80 R7 批量巡检（供 M14 健康检查/定时任务调用）。"""
    limit = int(body.get("limit") or q.get("limit") or 200)
    rows = db.query("SELECT id FROM record WHERE deleted_at IS NULL "
                    "ORDER BY id DESC LIMIT ?", (limit,))
    items, bad = [], []
    for r in rows:
        ok, v, why = verify_hash(r["id"])
        items.append({"record_id": r["id"], "valid": ok, "first_bad_version": v, "reason": why})
        if not ok:
            bad.append({"record_id": r["id"], "version_no": v, "reason": why})
    return {"checked": len(items), "invalid_count": len(bad), "invalid": bad, "items": items}


# ---------------- F76 版本查询 ----------------

@route("/api/record/versions", "GET", perm="record.view", module="M11",
       action="record.versions")
def versions(ctx, body, q):
    rid = q.get("record_id")
    require(rid, "缺少 record_id")
    _row(rid)
    rows = db.query("SELECT id, record_id, version_no, source, change_note, created_at, "
                    "created_by, created_role FROM record_version "
                    "WHERE record_id=? ORDER BY version_no", (rid,))
    return {"record_id": rid, "items": rows, "current_version": _max_version(rid)}


@route("/api/record/version/get", "GET", perm="record.view", module="M11",
       action="record.version.get")
def version_get(ctx, body, q):
    rid = q.get("record_id")
    require(rid, "缺少 record_id")
    vno = q.get("version_no")
    if not vno:
        vno = _max_version(rid)
    row = db.query_one("SELECT * FROM record_version WHERE record_id=? AND version_no=?",
                       (rid, int(vno)))
    if row is None:
        raise not_found("版本不存在")
    h = db.query_one("SELECT prev_hash, hash, algorithm FROM record_hash "
                     "WHERE record_id=? AND version_no=?", (rid, int(vno)))
    out = dict(row)
    out["snapshot"] = json.loads(row["snapshot_json"])
    out["prev_hash"] = h["prev_hash"] if h else None
    out["hash"] = h["hash"] if h else None
    return out


# ---------------- F83 导出 ----------------

def _export_dir():
    base = runtime.ROOT or os.path.dirname(os.path.dirname(db.path() or "")) or "."
    rel = config.get("record.export_dir", "data/exports") or "data/exports"
    return os.path.normpath(os.path.join(base, rel))


def _safe_target(base, name):
    """F83 R7：导出路径必须落在导出目录内，拒绝穿越与绝对路径。"""
    name = (name or "").strip()
    if not name:
        raise AppError(EXPORT_PATH_INVALID, "导出文件名不能为空", retryable=False,
                       http_status=400)
    parts = re.split(r"[\\/]+", name)
    if ".." in parts or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise AppError(EXPORT_PATH_INVALID, "导出路径非法（禁止 .. 或绝对路径）",
                       retryable=False, http_status=400)
    target = os.path.realpath(os.path.join(base, *parts))
    if not target.startswith(os.path.realpath(base) + os.sep):
        raise AppError(EXPORT_PATH_INVALID, "导出路径越出导出目录", retryable=False,
                       http_status=400)
    return target


def _u16(text):
    return (text or "").encode("utf-16-be").hex()


def _pdf_bytes(title, lines):
    """极简 PDF（1 页，标准 CJK 字体 STSong-Light + UniGB-UCS2-H，UTF-16BE 文本）。

    零第三方依赖；中文字形由阅读器提供的标准字体渲染（Acrobat/Edge 等可正常显示）。
    """
    parts = ["BT /F1 14 Tf 40 800 Td <%s> Tj ET" % _u16(title)]
    y = 774
    for ln in lines:
        parts.append("BT /F1 10 Tf 40 %d Td <%s> Tj ET" % (y, _u16(ln)))
        y -= 15
        if y < 50:
            break
    stream = "\n".join(parts).encode("latin-1")
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H "
        "/DescendantFonts [6 0 R] >>",
        "<< /Length %d >>\nstream\n" % len(stream),
        "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
        "/FontDescriptor 7 0 R >>",
        "<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
        "/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 880 /Descent -120 "
        "/CapHeight 700 /StemV 80 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body_txt in enumerate(objs, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode("latin-1")
        out += body_txt.encode("latin-1")
        if i == 5:
            out += stream
            out += b"\nendstream"
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("latin-1")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos)).encode("latin-1")
    return bytes(out)


def _snapshot_lines(snap):
    """把快照拍平成可读文本行（导出用）。"""
    lines = []
    rec = snap.get("record") or {}
    lines.append("病历号：%s   就诊：%s   患者：%s   类型：%s"
                 % (rec.get("id"), rec.get("visit_id"), rec.get("patient_id"),
                    rec.get("record_type")))
    lines.append("状态：%s   确认时间：%s" % (rec.get("status"), rec.get("confirmed_at")))
    lines.append("")
    cc = snap.get("chief_complaint") or {}
    lines.append("【主诉】%s %s" % (cc.get("content") or "", cc.get("duration") or ""))
    pi = snap.get("present_illness") or {}
    if pi.get("content"):
        lines.append("【现病史】%s" % pi["content"])
    tf = snap.get("tcm_four") or {}
    if any(tf.values()):
        lines.append("【中医四诊】望 %s｜闻 %s｜问 %s｜切 %s｜舌 %s/%s｜脉 %s｜体质 %s"
                     % (tf.get("inspection") or "-", tf.get("auscultation") or "-",
                        tf.get("interrogation") or "-", tf.get("palpation") or "-",
                        tf.get("tongue_body") or "-", tf.get("tongue_coating") or "-",
                        tf.get("pulse_desc") or "-", tf.get("constitution") or "-"))
    for d in snap.get("diagnoses") or []:
        tag = "西医" if d.get("diag_type") == "WEST" else "中医"
        name = d.get("icd10_name") or d.get("tcm_name") or ""
        lines.append("【诊断·%s】%s%s" % (tag, name, "（主诊断）" if d.get("is_primary") else ""))
    for s in snap.get("syndromes") or []:
        lines.append("【辨证】%s｜治则 %s｜治法 %s｜参考方 %s"
                     % (s.get("syndrome_name") or "", s.get("treatment_principle") or "-",
                        s.get("treatment_method") or "-", s.get("formula_ref") or "-"))
    for o in snap.get("orders") or []:
        lines.append("【医嘱】%s：%s" % (o.get("order_type"), o.get("content")))
    for a in snap.get("allergies") or []:
        lines.append("【过敏】%s %s" % (a.get("allergen"), a.get("manifestation") or ""))
    lines.append("")
    lines.append("—— 以上内容取自病历版本快照，未经二次编辑 ——")
    return lines


def _render_export(fmt, snap, version_no):
    lines = _snapshot_lines(snap)
    title = "病历导出 v%s" % version_no
    if fmt == "JSON":
        return json.dumps({"version_no": version_no, "snapshot": snap},
                          ensure_ascii=False, indent=2).encode("utf-8")
    if fmt == "HTML":
        esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;"))
        body = "".join("<div>%s</div>" % (esc(x) or "&nbsp;") for x in lines)
        return ("<!doctype html><meta charset='utf-8'><title>%s</title>"
                "<body style='font-family:sans-serif;padding:16px'>"
                "<h3>%s</h3>%s</body>" % (esc(title), esc(title), body)).encode("utf-8")
    return _pdf_bytes(title, lines)


@route("/api/record/export", "POST", perm="record.export", module="M11",
       action="record.export")
def export(ctx, body, q):
    """F83 导出：先校验 Hash 链 → 写文件 → 只记事件（不落内容副本）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    fmt = (body.get("format") or "HTML").upper()
    if fmt not in EXPORT_FORMATS:
        raise AppError(DATA_REQUIRED,
                       "导出格式必须是 %s 之一" % "/".join(EXPORT_FORMATS))
    key = body.get("idempotent_key")
    exist = _idem("record_export", key)
    if exist:
        return {"record_id": exist["record_id"], "export_id": exist["id"],
                "version_no": exist["version_no"], "format": exist["format"],
                "target_path": exist["target_path"], "duplicate": True}
    rec = _row(rid)
    version_no = int(body.get("version_no") or _max_version(rid))
    vrow = db.query_one("SELECT snapshot_json FROM record_version "
                        "WHERE record_id=? AND version_no=?", (rid, version_no))
    if vrow is None:
        raise not_found("版本不存在，无法导出")

    base = _export_dir()
    os.makedirs(base, exist_ok=True)
    name = body.get("target_path") or body.get("file_name") or \
        ("record_%s_v%d.%s" % (rid, version_no, fmt.lower()))
    target = _safe_target(base, name)

    _require_chain_ok(rid, "导出")          # 链异常：独立事务先落审计，再拒绝
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "record.export", "record", rid,
                              detail={"format": fmt, "version_no": version_no})

    snap = json.loads(vrow["snapshot_json"])
    data = _render_export(fmt, snap, version_no)
    with open(target, "wb") as f:
        f.write(data)
    try:
        with db.tx() as conn:
            now = tu.now_str()
            cur = db.execute(conn, """
                INSERT INTO record_export (record_id, version_no, format, target_path, file_size,
                                           hash_verified, exported_by, exported_role, exported_at,
                                           idempotent_key, created_at)
                VALUES (?,?,?,?,?,1,?,?,?,?,?)
            """, (rid, version_no, fmt, os.path.relpath(target, _export_dir()),
                  len(data), ctx.user_id, ctx.role_code, now, key, now))
            audit.audit_high_risk(conn, "EXPORT", "record", rid,
                                  "病历导出（格式 %s，版本 v%s）" % (fmt, version_no),
                                  {"target_path": os.path.relpath(target, _export_dir()),
                                   "file_size": len(data)})
    except Exception:
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    return {"record_id": rid, "version_no": version_no, "format": fmt,
            "target_path": os.path.relpath(target, _export_dir()), "file_size": len(data),
            "export_id": cur.lastrowid}


@route("/api/record/export/list", "GET", perm="record.view", module="M11",
       action="record.export.list")
def export_list(ctx, body, q):
    rid = q.get("record_id")
    sql = "SELECT * FROM record_export"
    params = ()
    if rid:
        sql += " WHERE record_id=?"
        params = (rid,)
    sql += " ORDER BY id DESC LIMIT 200"
    return {"items": db.query(sql, params)}
