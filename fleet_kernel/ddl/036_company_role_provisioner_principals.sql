-- Per-role Company admission provisioners.  This amendment creates no login
-- role or credential; the source-only provisioning helper does that under an
-- explicitly supplied bootstrap route after this schema is installed.

CREATE TABLE kernel_company_role_provisioner_principals (
    company_id TEXT NOT NULL CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    role_type TEXT NOT NULL CHECK (role_type IN (
        'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
    )),
    db_role NAME NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id,role_type)
);
REVOKE ALL ON kernel_company_role_provisioner_principals FROM PUBLIC;
ALTER TABLE kernel_company_role_provisioner_principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_role_provisioner_principals FORCE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_scope
ON kernel_company_role_provisioner_principals
USING (session_user='fleet_kernel_provisioner'::NAME)
WITH CHECK (session_user='fleet_kernel_provisioner'::NAME);

CREATE FUNCTION kernel_current_company_role_provisioner()
RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
    SELECT role_type
      FROM public.kernel_company_role_provisioner_principals
     WHERE company_id='__CC_SUITE_COMPANY_ID__' AND db_role=session_user::NAME
$$;
REVOKE ALL ON FUNCTION kernel_current_company_role_provisioner() FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_current_company()
RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
    SELECT company_id FROM (
        SELECT company_id FROM public.kernel_company_principals
         WHERE db_role=session_user::NAME
        UNION ALL
        SELECT company_id FROM public.kernel_company_reviewer_principals
         WHERE db_role=session_user::NAME
        UNION ALL
        SELECT company_id FROM public.kernel_company_review_attestor_principals
         WHERE db_role=session_user::NAME
        UNION ALL
        SELECT company_id FROM public.kernel_company_review_mac_signer_principals
         WHERE db_role=session_user::NAME
        UNION ALL
        SELECT company_id FROM public.kernel_company_worker_principals
         WHERE db_role=session_user::NAME
        UNION ALL
        SELECT company_id FROM public.kernel_company_role_provisioner_principals
         WHERE db_role=session_user::NAME
    ) company_scope LIMIT 1
$$;
REVOKE ALL ON FUNCTION kernel_current_company() FROM PUBLIC;

-- Replace admission-table policies with the exact mapped role scope.
DROP POLICY kernel_company_scope ON kernel_company_scribe_packages;
CREATE POLICY kernel_company_scope ON kernel_company_scribe_packages
USING (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
)
WITH CHECK (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
);

DROP POLICY kernel_company_scope ON kernel_company_scribe_admissions;
CREATE POLICY kernel_company_scope ON kernel_company_scribe_admissions
USING (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
)
WITH CHECK (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
);

DROP POLICY kernel_company_scope ON kernel_company_scribe_recovery_intents;
CREATE POLICY kernel_company_scope ON kernel_company_scribe_recovery_intents
USING (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
)
WITH CHECK (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
);

DROP POLICY kernel_company_scope ON kernel_company_role_admission_targets;
CREATE POLICY kernel_company_scope ON kernel_company_role_admission_targets
USING (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
)
WITH CHECK (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
);

DROP POLICY kernel_company_scope ON kernel_company_role_activation_subjects;
CREATE POLICY kernel_company_scope ON kernel_company_role_activation_subjects
USING (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
)
WITH CHECK (
    session_user='fleet_kernel_provisioner'::NAME
    OR company_id=kernel_current_company()
       AND (kernel_current_company_role_provisioner() IS NULL
            OR role_type=kernel_current_company_role_provisioner())
);

-- These identity relations had no RLS.  The permissive NULL branch preserves
-- their existing behavior for every non-provisioner principal; mapped role
-- provisioners see only their exact Company worker/session rows.
ALTER TABLE kernel_runtime_workers ENABLE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_role_provisioner_scope ON kernel_runtime_workers
USING (
    kernel_current_company_role_provisioner() IS NULL
    OR tenant_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
)
WITH CHECK (
    kernel_current_company_role_provisioner() IS NULL
    OR tenant_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
);

ALTER TABLE kernel_worker_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_role_provisioner_scope ON kernel_worker_sessions
USING (
    kernel_current_company_role_provisioner() IS NULL
    OR tenant_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
)
WITH CHECK (
    kernel_current_company_role_provisioner() IS NULL
    OR tenant_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
);

ALTER TABLE kernel_company_worker_principals ENABLE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_role_provisioner_scope
ON kernel_company_worker_principals
USING (
    kernel_current_company_role_provisioner() IS NULL
    OR company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
)
WITH CHECK (
    kernel_current_company_role_provisioner() IS NULL
    OR company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=kernel_current_company_role_provisioner()
);
