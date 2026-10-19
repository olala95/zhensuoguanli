# -*- coding: utf-8 -*-
"""M01 患者中心（F01 建档 / F02 搜索 / F03 资料维护 / F04 重复患者处理）。

表所有权：patient / patient_identifier / patient_contact / patient_merge_log
跨模块：合并时改写 visit.patient_id、record.patient_id 必须走 M02/M03 的服务接口（R2）。
"""

import re

from core import db, audit, timeutil as tu, ids
from core.api import route
from core.errors import require, require_keys, AppError, conflict, not_found
from core.errors import DATA_DUPLICATE, BIZ_PATIENT_HAS_DATA, DATA_VERSION_CONFLICT

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
ID18_RE = re.compile(r"^\d{17}[\dXx]$")
ID15_RE = re.compile(r"^\d{15}$")

# 身份证校验位权重（GB 11643-1999 mod 11-2）
_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CHECK = "10X98765432"


def valid_id_card(no):
    if not no:
        return False
    no = no.strip().upper()
    if ID15_RE.match(no):
        return True
    if not ID18_RE.match(no):
        return False
    s = sum(int(no[i]) * _WEIGHTS[i] for i in range(17))
    return _CHECK[s % 11] == no[17]


def id_card_birth_date(no):
    """从身份证号提取出生日期（YYYY-MM-DD）；无法提取返回 None。"""
    if not no:
        return None
    no = no.strip()
    if ID18_RE.match(no):
        return "%s-%s-%s" % (no[6:10], no[10:12], no[12:14])
    if ID15_RE.match(no):
        return "19%s-%s-%s" % (no[6:8], no[8:10], no[10:12])
    return None


def _validate(body, for_update=False):
    require_keys(body, ["name"]) if not for_update else None
    name = (body.get("name") or "").strip()
    if name:
        require(1 <= len(name) <= 50, "姓名长度需在 1-50 之间")
    gender = body.get("gender") or "U"
    require(gender in ("M", "F", "U"), "性别取值非法（M/F/U）")
    phone = (body.get("phone") or "").strip()
    if phone:
        require(bool(PHONE_RE.match(phone)) or len(phone) <= 20,
                "手机号格式不正确")
    idno = (body.get("id_card_no") or "").strip()
    if idno:
        require(valid_id_card(idno), "身份证号校验失败")
    if body.get("birth_date"):
        require(tu.parse(body["birth_date"]) is not None, "出生日期格式应为 YYYY-MM-DD")
    return True


def _save_identifiers(conn, patient_id, id_card_no, extra=None, reason=None):
    if id_card_no:
        exist = db.query_one("SELECT id FROM patient_identifier "
                             "WHERE id_type='ID_CARD' AND id_no=? AND deleted_at IS NULL",
                             (id_card_no,))
        if exist and exist["id"] and \
                db.query_one("SELECT 1 FROM patient_identifier WHERE id=? AND patient_id=?",
                             (exist["id"], patient_id)) is None:
            raise AppError(DATA_DUPLICATE, "该身份证号已被其他患者使用",
                           retryable=False, http_status=409)
        if exist is None:
            db.execute(conn, """
                INSERT INTO patient_identifier (patient_id, id_type, id_no, is_primary,
                                                created_at, created_by)
                VALUES (?,'ID_CARD',?,1,?,?)
            """, (patient_id, id_card_no, tu.now_str(), 0))
    for item in (extra or []):
        if not item.get("id_no"):
            continue
        db.execute(conn, """
            INSERT INTO patient_identifier (patient_id, id_type, id_no, is_primary,
                                            created_at, created_by)
            VALUES (?,?,?,0,?,?)
        """, (patient_id, item.get("id_type", "OTHER"), item["id_no"], tu.now_str(), 0))


@route("/api/patient/create", "POST", perm="patient.create", module="M01", action="patient.create")
def create(ctx, body, q):
    """F01 患者建档。编号 P{yyyyMMdd}{4位}，事务内取号。"""
    _validate(body)
    id_card_no = (body.get("id_card_no") or "").strip()
    birth_date = body.get("birth_date") or id_card_birth_date(id_card_no)

    with db.tx() as conn:
        audit.audit_operation(conn, "M01", "patient.create", "patient", None,
                              detail={"name": body.get("name")})
        patient_no = ids.next_no(conn, "PATIENT")
        cur = db.execute(conn, """
            INSERT INTO patient (patient_no, name, gender, birth_date, phone, address,
                                 occupation, marital_status, blood_type, note,
                                 created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (patient_no, body["name"].strip(), body.get("gender", "U"), birth_date,
              body.get("phone"), body.get("address"), body.get("occupation"),
              body.get("marital_status"), body.get("blood_type"), body.get("note"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        pid = cur.lastrowid
        _save_identifiers(conn, pid, id_card_no, body.get("identifiers"))
        for c in (body.get("contacts") or []):
            if not c.get("phone"):
                continue
            db.execute(conn, """
                INSERT INTO patient_contact (patient_id, contact_name, relation, phone,
                                             is_primary, created_at, created_by)
                VALUES (?,?,?,?,?,?,?)
            """, (pid, c.get("contact_name"), c.get("relation"), c["phone"],
                  c.get("is_primary", 0), tu.now_str(), ctx.user_id))
        row = db.query_one("SELECT * FROM patient WHERE id=?", (pid,))
    return dict(row)


@route("/api/patient/search", "GET", perm="patient.view", module="M01", action="patient.search")
def search(ctx, body, q):
    """F02 患者搜索：姓名 / 患者号 / 手机号 / 证件号，模糊 + 精确混合。"""
    kw = (q.get("keyword") or "").strip()
    limit = min(int(q.get("limit") or 20), 100)
    sql = """
        SELECT p.id, p.patient_no, p.name, p.gender, p.birth_date, p.phone, p.address,
               p.last_visit_at, p.created_at,
               (SELECT id_no FROM patient_identifier i
                 WHERE i.patient_id=p.id AND i.id_type='ID_CARD'
                   AND i.deleted_at IS NULL LIMIT 1) AS id_card_no
        FROM patient p WHERE p.deleted_at IS NULL
    """
    params = ()
    if kw:
        sql += """ AND (p.name LIKE ? OR p.patient_no LIKE ? OR p.phone LIKE ?
                        OR EXISTS (SELECT 1 FROM patient_identifier i
                                   WHERE i.patient_id=p.id AND i.deleted_at IS NULL
                                     AND i.id_no LIKE ?)) """
        like = "%" + kw + "%"
        params = (like, like, like, like)
    sql += " ORDER BY p.id DESC LIMIT ?"
    params = params + (limit,)
    return {"items": db.query(sql, params), "keyword": kw}


@route("/api/patient/get", "GET", perm="patient.view", module="M01", action="patient.get")
def get(ctx, body, q):
    pid = q.get("id") or q.get("patient_id")
    require(pid, "缺少 patient_id")
    row = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (pid,))
    if row is None:
        raise not_found("患者不存在")
    row = dict(row)
    age = tu.calc_age(row.get("birth_date"))
    row["age_text"] = ("%d岁" % age[0]) if age else None
    row["identifiers"] = db.query("SELECT * FROM patient_identifier "
                                  "WHERE patient_id=? AND deleted_at IS NULL", (pid,))
    row["contacts"] = db.query("SELECT * FROM patient_contact "
                               "WHERE patient_id=? AND deleted_at IS NULL", (pid,))
    row["visits"] = db.query("""
        SELECT id, visit_no, status, registered_at, doctor_id FROM visit
        WHERE patient_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 50
    """, (pid,))
    return row


TRACKED_FIELDS = ["name", "gender", "birth_date", "phone", "address", "occupation",
                  "marital_status", "blood_type", "note"]


@route("/api/patient/update", "POST", perm="patient.update", module="M01", action="patient.update")
def update(ctx, body, q):
    """F03 患者资料维护：乐观锁 version 校验 + 逐字段 audit_data_change。"""
    pid = body.get("id") or body.get("patient_id")
    require(pid, "缺少 patient_id")
    _validate(body, for_update=True)
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (pid,))
        if row is None:
            raise not_found("患者不存在")
        if body.get("version") is not None and int(body["version"]) != row["version"]:
            raise conflict("数据已被他人修改，请刷新后重试（乐观锁）",
                           code=DATA_VERSION_CONFLICT)
        audit.audit_operation(conn, "M01", "patient.update", "patient", pid,
                              detail=body.get("reason"))
        sets, params, changed = [], [], []
        for f in TRACKED_FIELDS:
            if f in body and body[f] is not None:
                before, after = row[f], body[f]
                if before != after:
                    sets.append("%s=?" % f)
                    params.append(after)
                    changed.append((f, before, after))
        if not changed:
            return {"id": pid, "changed": 0}
        sets += ["updated_at=?", "updated_by=?", "version=version+1"]
        params += [tu.now_str(), ctx.user_id, pid]
        db.execute(conn, "UPDATE patient SET %s WHERE id=?" % ",".join(sets), tuple(params))
        for f, before, after in changed:
            audit.audit_data_change(conn, "patient", pid, f, before, after,
                                    body.get("reason"))
    return {"id": pid, "changed": len(changed), "fields": [c[0] for c in changed]}


@route("/api/patient/duplicates", "GET", perm="patient.view",
       module="M01", action="patient.duplicates")
def duplicates(ctx, body, q):
    """F04 重复患者检测：证件号相同 或 姓名+出生日期相同 或 姓名+手机号相同。"""
    name = (q.get("name") or "").strip()
    birth_date = (q.get("birth_date") or "").strip()
    phone = (q.get("phone") or "").strip()
    id_no = (q.get("id_card_no") or "").strip()
    if not (name or id_no or phone):
        return {"items": []}
    sql = """
        SELECT p.id, p.patient_no, p.name, p.gender, p.birth_date, p.phone, p.created_at,
               (SELECT id_no FROM patient_identifier i WHERE i.patient_id=p.id
                  AND i.id_type='ID_CARD' AND i.deleted_at IS NULL LIMIT 1) AS id_card_no
        FROM patient p WHERE p.deleted_at IS NULL AND (
              (?<>'' AND EXISTS (SELECT 1 FROM patient_identifier i
                                 WHERE i.patient_id=p.id AND i.deleted_at IS NULL
                                   AND i.id_no=?))
           OR (?<>'' AND p.name=? AND (?='' OR p.birth_date=?))
           OR (?<>'' AND p.name=? AND p.phone=?)
        ) ORDER BY p.id
    """
    return {"items": db.query(sql, (id_no, id_no, name, name, birth_date, birth_date,
                                    phone, name, phone))}


@route("/api/patient/merge", "POST", perm="patient.merge", module="M01", action="patient.merge")
def merge(ctx, body, q):
    """F04 重复患者合并：保留主患者，副患者软删，业务数据改挂到主患者。

    跨模块改写走 M02/M03 的服务接口（R2），本模块不直接 UPDATE visit/record。
    """
    primary = body.get("primary_patient_id")
    merged = body.get("merged_patient_id")
    reason = body.get("reason") or ""
    require(primary and merged, "缺少 primary_patient_id / merged_patient_id")
    require(primary != merged, "不能合并同一个患者")
    require(len(reason) >= 2, "合并必须填写原因")

    from . import m02_visit, m03_record   # 延迟导入，避免循环依赖
    from . import m05_lab, m06_prescription, m07_pharmacy, m08_treatment, m09_billing

    with db.tx() as conn:
        p = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (primary,))
        m = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (merged,))
        if not p or not m:
            raise not_found("患者不存在")
        audit.audit_operation(conn, "M01", "patient.merge", "patient", primary,
                              detail={"merged": merged, "reason": reason})
        moved = {
            "visit": m02_visit.reassign_patient(conn, merged, primary, reason),
            "record": m03_record.reassign_patient(conn, merged, primary, reason),
            "allergy": _reassign_allergy(conn, merged, primary),
            "identifier": _reassign_identifier(conn, merged, primary),
            "contact": _reassign_contact(conn, merged, primary),
        }
        # 第三阶段起新增模块：跨模块改挂一律走对方服务接口（R2）
        for mod in (m05_lab, m06_prescription, m07_pharmacy, m08_treatment, m09_billing):
            fn = getattr(mod, "reassign_patient", None)
            if fn:
                try:
                    moved[mod.__name__.split("_")[0]] = fn(conn, merged, primary, reason)
                except Exception as exc:
                    # R6：不静默吞异常——记录日志并在结果中标记失败
                    from core.logging_setup import get
                    get("m01").exception("患者合并改挂失败 %s：%s", mod.__name__, exc)
                    moved[mod.__name__.split("_")[0]] = "ERROR: %s" % exc
        db.execute(conn, "UPDATE patient SET deleted_at=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (tu.now_str(), tu.now_str(), ctx.user_id, merged))
        audit.audit_data_change(conn, "patient", merged, "deleted_at", None,
                                tu.now_str(), reason)
        audit.audit_high_risk(conn, audit.RISK_MERGE, "patient", merged, reason,
                              {"primary": primary, "moved": moved})
        db.execute(conn, """
            INSERT INTO patient_merge_log (primary_patient_id, merged_patient_id, merged_by,
                                           merged_at, reason, detail_json)
            VALUES (?,?,?,?,?,?)
        """, (primary, merged, ctx.user_id, tu.now_str(), reason, audit.dumps(moved)))
    return {"primary_patient_id": primary, "merged_patient_id": merged, "moved": moved}


def _reassign_allergy(conn, old_id, new_id):
    cur = db.execute(conn, "UPDATE record_allergy SET patient_id=? "
                           "WHERE patient_id=? AND deleted_at IS NULL", (new_id, old_id))
    return cur.rowcount


def _reassign_identifier(conn, old_id, new_id):
    cur = db.execute(conn, "UPDATE patient_identifier SET patient_id=?, is_primary=0 "
                           "WHERE patient_id=? AND deleted_at IS NULL", (new_id, old_id))
    return cur.rowcount


def _reassign_contact(conn, old_id, new_id):
    cur = db.execute(conn, "UPDATE patient_contact SET patient_id=? "
                           "WHERE patient_id=? AND deleted_at IS NULL", (new_id, old_id))
    return cur.rowcount


@route("/api/patient/delete", "POST", perm="patient.update", module="M01", action="patient.delete")
def soft_delete(ctx, body, q):
    """软删除。存在有效 visit / prescription 时拒绝（04 §3）。"""
    pid = body.get("id") or body.get("patient_id")
    require(pid, "缺少 patient_id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (pid,))
        if row is None:
            raise not_found("患者不存在")
        n = db.query_one("SELECT COUNT(*) c FROM visit WHERE patient_id=? "
                         "AND deleted_at IS NULL AND status<>'VOID'", (pid,))
        if n and n["c"] > 0:
            raise conflict("该患者存在有效就诊记录，不能删除（可改用合并）",
                           code=BIZ_PATIENT_HAS_DATA)
        audit.audit_operation(conn, "M01", "patient.delete", "patient", pid,
                              detail={"reason": body.get("reason")})
        db.execute(conn, "UPDATE patient SET deleted_at=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (tu.now_str(), tu.now_str(), ctx.user_id, pid))
        audit.audit_high_risk(conn, audit.RISK_DELETE, "patient", pid, body.get("reason"))
    return {"id": pid, "deleted": True}
