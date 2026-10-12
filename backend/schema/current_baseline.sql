-- TarifeAI current-schema baseline.
-- Fresh installations only. Catalog rows/embeddings are ingested separately.
DO $$
BEGIN
    IF to_regclass('public.packages') IS NOT NULL
       OR to_regclass('public.message_usage') IS NOT NULL
       OR to_regclass('public.quota_reservation') IS NOT NULL THEN
        RAISE EXCEPTION 'TarifeAI application tables already exist; baseline install refused';
    END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tarifeai_backend') THEN
        CREATE ROLE tarifeai_backend NOLOGIN
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tarifeai_backend'
               AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
                    OR rolinherit OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'tarifeai_backend exists with unsafe attributes';
    END IF;
END
$$;

CREATE TABLE public.packages (
    id uuid PRIMARY KEY,
    operator varchar(32) NOT NULL,
    name varchar(256) NOT NULL,
    line_type varchar(16) NOT NULL,
    customer_type varchar(16) NOT NULL,
    operator_family varchar(48),
    audience_segment varchar(24),
    product_scope varchar(24) NOT NULL,
    age_min integer,
    age_max integer,
    eligibility_tags jsonb,
    price_try numeric(10,2),
    price_period varchar(16),
    commitment_months integer,
    commitment_status varchar(20) NOT NULL,
    price_lock_months integer,
    no_commitment_price_try numeric(10,2),
    internet_gb numeric(8,2),
    social_media_gb numeric(8,2),
    bonus_internet_gb numeric(8,2),
    voice_minutes integer,
    sms_count integer,
    validity_days integer,
    conditions text,
    campaign text,
    source_url text NOT NULL,
    verified_at timestamptz NOT NULL,
    doc_text text NOT NULL,
    embedding vector(1024),
    activation_channels jsonb,
    benefits jsonb,
    benefits_status varchar(16) NOT NULL DEFAULT 'unverified',
    social_media_apps jsonb,
    promo_months integer,
    price_after_promo_try numeric(10,2),
    embedding_model varchar(128),
    doc_hash varchar(64),
    bonus_internet_scope varchar(24),
    renewal_type varchar(16) NOT NULL DEFAULT 'unknown',
    CONSTRAINT uq_packages_identity UNIQUE (operator, line_type, name),
    CONSTRAINT ck_packages_operator CHECK (operator IN ('turkcell','vodafone','turk_telekom')),
    CONSTRAINT ck_packages_line_type CHECK (line_type IN ('prepaid','postpaid')),
    CONSTRAINT ck_packages_customer_type CHECK (customer_type IN ('new','existing','any')),
    CONSTRAINT ck_packages_commitment_status CHECK (commitment_status IN ('committed','no_commitment','unknown')),
    CONSTRAINT ck_packages_price_period CHECK (price_period IS NULL OR price_period IN ('month','28_day','60_day','90_day','180_day','360_day')),
    CONSTRAINT ck_packages_price_positive CHECK (price_try IS NULL OR price_try > 0),
    CONSTRAINT ck_packages_quantities CHECK (
        (internet_gb IS NULL OR internet_gb >= 0 OR internet_gb = -1) AND
        (social_media_gb IS NULL OR social_media_gb >= 0 OR social_media_gb = -1) AND
        (bonus_internet_gb IS NULL OR bonus_internet_gb >= 0 OR bonus_internet_gb = -1) AND
        (voice_minutes IS NULL OR voice_minutes >= 0 OR voice_minutes = -1) AND
        (sms_count IS NULL OR sms_count >= 0 OR sms_count = -1)),
    CONSTRAINT ck_packages_age_range CHECK (age_min IS NULL OR age_max IS NULL OR age_min <= age_max),
    CONSTRAINT ck_packages_prepaid_no_commitment CHECK (line_type <> 'prepaid' OR (commitment_months IS NULL AND no_commitment_price_try IS NULL)),
    CONSTRAINT ck_packages_benefits_status CHECK (benefits_status IN ('verified','verified_none','unverified')),
    CONSTRAINT ck_packages_promo_pair CHECK ((promo_months IS NULL) = (price_after_promo_try IS NULL) AND (promo_months IS NULL OR promo_months > 0)),
    CONSTRAINT ck_packages_json_arrays CHECK (
        (activation_channels IS NULL OR jsonb_typeof(activation_channels) = 'array') AND
        (social_media_apps IS NULL OR jsonb_typeof(social_media_apps) = 'array') AND
        (eligibility_tags IS NULL OR jsonb_typeof(eligibility_tags) = 'array') AND
        (benefits IS NULL OR jsonb_typeof(benefits) = 'array')),
    CONSTRAINT ck_packages_bonus_scope CHECK (
        (bonus_internet_gb IS NULL) = (bonus_internet_scope IS NULL) AND
        (bonus_internet_scope IS NULL OR bonus_internet_scope IN ('general','tethering_only','app_specific'))),
    CONSTRAINT ck_packages_renewal_type CHECK (renewal_type IN ('recurring','one_time','unknown')),
    CONSTRAINT ck_packages_period_by_line CHECK (
        CASE WHEN line_type = 'prepaid' THEN price_period = validity_days || '_day'
             ELSE price_period = 'month' END),
    CONSTRAINT ck_packages_eligibility_tags CHECK (
        eligibility_tags IS NULL OR NOT (eligibility_tags ?| ARRAY['new_line','number_porting','age_restricted']))
);
CREATE INDEX ix_packages_operator ON public.packages (operator);
CREATE INDEX ix_packages_line_type ON public.packages (line_type);
CREATE INDEX ix_packages_customer_type ON public.packages (customer_type);
CREATE INDEX ix_packages_operator_family ON public.packages (operator_family);
CREATE INDEX ix_packages_audience_segment ON public.packages (audience_segment);
CREATE INDEX ix_packages_product_scope ON public.packages (product_scope);

CREATE TABLE public.message_usage (
    id uuid PRIMARY KEY, user_id uuid NOT NULL, ip varchar(64),
    is_anonymous boolean NOT NULL, created_at timestamptz NOT NULL
);
CREATE INDEX ix_message_usage_user_id ON public.message_usage (user_id);
CREATE INDEX ix_message_usage_ip ON public.message_usage (ip);
CREATE INDEX ix_message_usage_created_at ON public.message_usage (created_at);

CREATE TABLE public.quota_reservation (
    id uuid PRIMARY KEY, user_id uuid NOT NULL, ip varchar(64),
    is_anonymous boolean NOT NULL, expires_at timestamptz NOT NULL
);
CREATE INDEX ix_quota_reservation_user_id ON public.quota_reservation (user_id);
CREATE INDEX ix_quota_reservation_ip ON public.quota_reservation (ip);
CREATE INDEX ix_quota_reservation_expires_at ON public.quota_reservation (expires_at);

REVOKE ALL PRIVILEGES ON public.packages, public.message_usage, public.quota_reservation FROM anon, authenticated;
REVOKE CREATE ON SCHEMA public FROM tarifeai_backend;
GRANT USAGE ON SCHEMA public TO tarifeai_backend;
GRANT SELECT ON public.packages TO tarifeai_backend;
GRANT SELECT, INSERT, DELETE ON public.message_usage, public.quota_reservation TO tarifeai_backend;
ALTER TABLE public.packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.quota_reservation ENABLE ROW LEVEL SECURITY;
CREATE POLICY tarifeai_backend_select ON public.packages FOR SELECT TO tarifeai_backend USING (true);
CREATE POLICY tarifeai_backend_select ON public.message_usage FOR SELECT TO tarifeai_backend USING (true);
CREATE POLICY tarifeai_backend_insert ON public.message_usage FOR INSERT TO tarifeai_backend WITH CHECK (true);
CREATE POLICY tarifeai_backend_delete ON public.message_usage FOR DELETE TO tarifeai_backend USING (true);
CREATE POLICY tarifeai_backend_select ON public.quota_reservation FOR SELECT TO tarifeai_backend USING (true);
CREATE POLICY tarifeai_backend_insert ON public.quota_reservation FOR INSERT TO tarifeai_backend WITH CHECK (true);
CREATE POLICY tarifeai_backend_delete ON public.quota_reservation FOR DELETE TO tarifeai_backend USING (true);
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
