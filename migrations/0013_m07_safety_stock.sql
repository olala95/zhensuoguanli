-- 0013 第三阶段补充字段（只增不改，04 §5）
-- F50 库存预警需要品目级安全库存阈值（品目阈值 > 全局默认 stock.safety_stock_default）
-- 这两列在 0010 设计阶段遗漏，按迁移规则以新增脚本补齐，不修改 0007/0010

ALTER TABLE drug ADD COLUMN safety_stock REAL;
ALTER TABLE herb ADD COLUMN safety_stock_g REAL;
