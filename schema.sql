CREATE TABLE IF NOT EXISTS ai_gateway_eval_results (
  id            bigserial PRIMARY KEY,
  tested_at     timestamptz NOT NULL DEFAULT now(),
  tested_date   date        NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai')::date,
  domain        text        NOT NULL,
  model         text        NOT NULL,
  model_rate    numeric(6,3),
  recharge_rate numeric(6,3),
  request_ip    inet,
  fingerprint   text,
  top_model     text,
  candy         text,
  candy_answers text,
  verdict       text,
  note          text
);

-- Safe for installations that already have the original table.
ALTER TABLE ai_gateway_eval_results ADD COLUMN IF NOT EXISTS tested_date date;
UPDATE ai_gateway_eval_results
SET tested_date = (tested_at AT TIME ZONE 'Asia/Shanghai')::date
WHERE tested_date IS NULL;
ALTER TABLE ai_gateway_eval_results ALTER COLUMN tested_date SET NOT NULL;
ALTER TABLE ai_gateway_eval_results ALTER COLUMN tested_date SET DEFAULT (now() AT TIME ZONE 'Asia/Shanghai')::date;

ALTER TABLE ai_gateway_eval_results ADD COLUMN IF NOT EXISTS time_precision text NOT NULL DEFAULT 'timestamp'
  CHECK (time_precision IN ('timestamp', 'date'));

-- A browser-generated identifier makes retrying a failed upload idempotent.
ALTER TABLE ai_gateway_eval_results ADD COLUMN IF NOT EXISTS result_uuid uuid NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_uuid ON ai_gateway_eval_results (result_uuid);

-- Keep a stable grouping identifier and a short display prefix, never the secret.
-- Older records remain NULL because their original credentials were not stored.
ALTER TABLE ai_gateway_eval_results ADD COLUMN IF NOT EXISTS key_hash varchar(64);
ALTER TABLE ai_gateway_eval_results ADD COLUMN IF NOT EXISTS key_prefix varchar(6);
CREATE INDEX IF NOT EXISTS idx_eval_key_dmt
  ON ai_gateway_eval_results (key_hash, domain, model, tested_at DESC);

CREATE INDEX IF NOT EXISTS idx_eval_dmt
  ON ai_gateway_eval_results (domain, model, tested_at DESC);

CREATE INDEX IF NOT EXISTS idx_eval_date
  ON ai_gateway_eval_results (tested_date DESC);

CREATE INDEX IF NOT EXISTS idx_eval_tested_at
  ON ai_gateway_eval_results (tested_at DESC);
