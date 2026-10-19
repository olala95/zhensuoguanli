-- 0015 修正 v_revenue_daily：补 visit_id / patient_id
-- 原因：F120「医生工作量」需要把营收归集到接诊医生，而营收只能经视图取数（R-M15-1）。
-- 0014 版的视图只有 bill_id，无法在不碰业务表的前提下关联到 visit → 用新迁移重建视图。
-- 迁移脚本只增不改，因此这里 DROP + CREATE，而不是修改 0014。

DROP VIEW IF EXISTS v_revenue_daily;

CREATE VIEW IF NOT EXISTS v_revenue_daily AS
SELECT substr(p.paid_at, 1, 10) AS biz_date,
       p.channel                AS channel,
       'PAY'                    AS kind,
       p.amount_fen             AS amount_fen,
       p.bill_id                AS bill_id,
       b.visit_id               AS visit_id,
       b.patient_id             AS patient_id,
       p.id                     AS ref_id,
       p.paid_at                AS occurred_at
FROM payment p
LEFT JOIN bill b ON b.id = p.bill_id
WHERE p.status = 'SUCCESS' AND p.paid_at IS NOT NULL
UNION ALL
SELECT substr(r.occurred_at, 1, 10),
       'REFUND',
       'REFUND',
       -r.total_amount_fen,
       r.bill_id,
       b.visit_id,
       r.patient_id,
       r.id,
       r.occurred_at
FROM refund r
LEFT JOIN bill b ON b.id = r.bill_id
WHERE r.status = 'SUCCEEDED' AND r.deleted_at IS NULL AND r.occurred_at IS NOT NULL;
