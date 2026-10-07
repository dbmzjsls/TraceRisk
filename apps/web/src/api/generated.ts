/** Generated from local TraceRisk OpenAPI. Run pnpm api:generate; do not edit. */
export interface paths {
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Login */
        post: operations["login_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_auth_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/companies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Companies */
        get: operations["list_companies_companies_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/companies/{company_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Company */
        get: operations["get_company_companies__company_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Health
         * @description Process liveness only; database readiness is checked separately.
         */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/investigations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Investigations */
        get: operations["list_investigations_investigations_get"];
        put?: never;
        /** Create */
        post: operations["create_investigations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/investigations/{investigation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detail */
        get: operations["detail_investigations__investigation_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/investigations/{investigation_id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rerun */
        post: operations["rerun_investigations__investigation_id__runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/reports/{report_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Report */
        get: operations["get_report_reports__report_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/reviews/pending": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Pending */
        get: operations["pending_reviews_pending_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/reviews/{finding_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review */
        post: operations["review_reviews__finding_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Run */
        post: operations["review_run_runs__run_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/workflow-runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Workflow Run Events */
        get: operations["workflow_run_events_workflow_runs__run_id__events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** CompanyListResponse */
        CompanyListResponse: {
            /** Items */
            items: components["schemas"]["CompanyResponse"][];
            /**
             * Notice
             * @default 合成数据，仅供演示
             */
            notice: string;
        };
        /** CompanyResponse */
        CompanyResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Synthetic */
            is_synthetic: boolean;
            /** Name */
            name: string;
            /** Unified Credit Code */
            unified_credit_code: string;
        };
        /** ConflictResponse */
        ConflictResponse: {
            /** Authority Source */
            authority_source: string | null;
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Fact Key */
            fact_key: string;
            /** Fact Type */
            fact_type: string;
            /**
             * Resolution State
             * @enum {string}
             */
            resolution_state: "resolved" | "unresolved";
            /** Resolved Value */
            resolved_value: string | null;
            /** Risk Type */
            risk_type: string;
            /** Sides */
            sides: components["schemas"]["ConflictSideResponse"][];
        };
        /** ConflictSideResponse */
        ConflictSideResponse: {
            /** Content Hash */
            content_hash: string;
            /**
             * Evidence Id
             * Format: uuid
             */
            evidence_id: string;
            /** Fact Value */
            fact_value: string;
            /**
             * Observed At
             * Format: date
             */
            observed_at: string;
            /** Source Type */
            source_type: string;
        };
        /** CoverageEntryResponse */
        CoverageEntryResponse: {
            /** Checked At */
            checked_at: string | null;
            /** Coverage Id */
            coverage_id: string | null;
            /**
             * Source Type
             * @enum {string}
             */
            source_type: "business" | "judicial" | "news" | "intellectual_property";
            /**
             * Status
             * @enum {string}
             */
            status: "available" | "unavailable" | "stale" | "missing";
        };
        /** CoverageResponse */
        CoverageResponse: {
            /** Entries */
            entries: components["schemas"]["CoverageEntryResponse"][];
            /**
             * Evidence Sufficiency
             * @enum {string}
             */
            evidence_sufficiency: "sufficient" | "insufficient" | "unavailable";
            /** Insufficiency Details */
            insufficiency_details: string[];
        };
        /** CurrentRunResponse */
        CurrentRunResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Report Id */
            report_id: string | null;
            /** Run Number */
            run_number: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "awaiting_review" | "resume_queued" | "completed" | "failed";
        };
        /** DecisionResponse */
        DecisionResponse: {
            /** Ai Finding Id */
            ai_finding_id: string | null;
            /**
             * Decision
             * @enum {string}
             */
            decision: "approve" | "modify" | "reject";
            /** Finding Version */
            finding_version: number | null;
            /** Human Finding Version Id */
            human_finding_version_id?: string | null;
            /** Human Version */
            human_version?: number | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reason */
            reason: string;
            /** Replacement */
            replacement?: {
                [key: string]: unknown;
            } | null;
            /**
             * Reviewer Id
             * Format: uuid
             */
            reviewer_id: string;
        };
        /** EvidenceCitation */
        EvidenceCitation: {
            /**
             * Chunk Id
             * Format: uuid
             */
            chunk_id: string;
            /** Content Hash */
            content_hash: string;
            /**
             * Evidence Id
             * Format: uuid
             */
            evidence_id: string;
            /** Quoted Excerpt */
            quoted_excerpt: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthResponse */
        HealthResponse: {
            /**
             * Status
             * @default ok
             * @constant
             */
            status: "ok";
        };
        /** InvestigationCreateRequest */
        InvestigationCreateRequest: {
            /**
             * As Of Date
             * Format: date
             */
            as_of_date: string;
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
        };
        /** InvestigationDetailResponse */
        InvestigationDetailResponse: {
            /** Ai Findings */
            ai_findings: components["schemas"]["PendingFindingResponse"][];
            /**
             * As Of Date
             * Format: date
             */
            as_of_date: string;
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Company Name */
            company_name: string;
            /** Conflicts */
            conflicts: components["schemas"]["ConflictResponse"][];
            coverage: components["schemas"]["CoverageResponse"];
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            current_run: components["schemas"]["CurrentRunResponse"];
            /** Decisions */
            decisions: components["schemas"]["DecisionResponse"][];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Retrieval State
             * @enum {string}
             */
            retrieval_state: "pending" | "legacy_unavailable" | "ready";
            /** Retrieved Evidence */
            retrieved_evidence: components["schemas"]["RetrievedEvidenceResponse"][];
            /** Rule Findings */
            rule_findings: components["schemas"]["RuleFindingResponse"][];
            /** Rule Score */
            rule_score: number;
            /** Steps */
            steps: components["schemas"]["StepResponse"][];
        };
        /** InvestigationListResponse */
        InvestigationListResponse: {
            /** Items */
            items: components["schemas"]["InvestigationResponse"][];
        };
        /** InvestigationResponse */
        InvestigationResponse: {
            /**
             * As Of Date
             * Format: date
             */
            as_of_date: string;
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Company Name */
            company_name: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            current_run: components["schemas"]["CurrentRunResponse"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /** LoginRequest */
        LoginRequest: {
            /**
             * Password
             * Format: password
             */
            password: string;
            /** Username */
            username: string;
        };
        /** PendingFindingResponse */
        PendingFindingResponse: {
            /** Citations */
            citations: {
                [key: string]: unknown;
            }[];
            /** Claim Code */
            claim_code: string;
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Company Name */
            company_name: string;
            /** Confidence Level */
            confidence_level: string;
            /** Evidence Sufficiency */
            evidence_sufficiency: string;
            /**
             * Finding Id
             * Format: uuid
             */
            finding_id: string;
            /** Finding Key */
            finding_key: string;
            /** Finding Version */
            finding_version: number;
            /**
             * Investigation Id
             * Format: uuid
             */
            investigation_id: string;
            /** Reasoning */
            reasoning: string;
            /** Review Recommended */
            review_recommended: boolean;
            /** Risk Type */
            risk_type: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /** Summary */
            summary: string;
            /**
             * Support Reason
             * @enum {string}
             */
            support_reason: "canonical_fact_match" | "unverified_free_text_extension" | "legacy-stage3";
            /**
             * Support Status
             * @enum {string}
             */
            support_status: "supported" | "indeterminate";
            /** Title */
            title: string;
        };
        /** ReportResponse */
        ReportResponse: {
            /** Html Content */
            html_content: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /** Snapshot */
            snapshot: {
                [key: string]: unknown;
            };
        };
        /** RerunResponse */
        RerunResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Run Number */
            run_number: number;
            /**
             * Status
             * @default queued
             * @constant
             */
            status: "queued";
        };
        /** RetrievedEvidenceResponse */
        RetrievedEvidenceResponse: {
            /**
             * Chunk Id
             * Format: uuid
             */
            chunk_id: string;
            /** Content */
            content: string;
            /** Content Hash */
            content_hash: string;
            /**
             * Evidence Id
             * Format: uuid
             */
            evidence_id: string;
            /** Fact Key */
            fact_key: string;
            /** Fact Type */
            fact_type: string;
            /** Fact Value */
            fact_value: string;
            /**
             * Observed At
             * Format: date
             */
            observed_at: string;
            /** Polarity */
            polarity: string;
            /** Rank */
            rank: number;
            /** Risk Type */
            risk_type: string;
            /** Source Uri */
            source_uri: string;
            /** Title */
            title: string;
        };
        /** ReviewRequest */
        ReviewRequest: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "approve" | "modify" | "reject";
            /** Finding Version */
            finding_version: number;
            /** Reason */
            reason: string;
            replacement?: components["schemas"]["RiskFindingDraft"] | null;
        };
        /** ReviewResponse */
        ReviewResponse: {
            /**
             * Decision Id
             * Format: uuid
             */
            decision_id: string;
        };
        /** RiskFindingDraft */
        RiskFindingDraft: {
            /** Claim Code */
            claim_code: string;
            /**
             * Confidence Level
             * @enum {string}
             */
            confidence_level: "low" | "medium" | "high";
            /** Evidence Citations */
            evidence_citations: components["schemas"]["EvidenceCitation"][];
            /**
             * Evidence Sufficiency
             * @enum {string}
             */
            evidence_sufficiency: "sufficient" | "insufficient";
            /** Finding Key */
            finding_key: string;
            /** Reasoning */
            reasoning: string;
            /** Review Recommended */
            review_recommended: boolean;
            /**
             * Risk Type
             * @enum {string}
             */
            risk_type: "business" | "judicial" | "news" | "intellectual_property";
            /** Summary */
            summary: string;
            /** Title */
            title: string;
        };
        /**
         * Role
         * @enum {string}
         */
        Role: "analyst" | "reviewer" | "admin";
        /** RuleEvidenceResponse */
        RuleEvidenceResponse: {
            /** Chunk Content Hash */
            chunk_content_hash: string | null;
            /** Chunk Id */
            chunk_id: string | null;
            /** Content Hash */
            content_hash: string;
            /**
             * Evidence Id
             * Format: uuid
             */
            evidence_id: string;
        };
        /** RuleFindingResponse */
        RuleFindingResponse: {
            /** Evidence */
            evidence: components["schemas"]["RuleEvidenceResponse"][];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Parameters */
            parameters: {
                [key: string]: unknown;
            };
            /** Rule Code */
            rule_code: string;
            /** Rule Version */
            rule_version: string;
            /** Score */
            score: number;
        };
        /** RunReviewRequest */
        RunReviewRequest: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "approve" | "reject";
            /** Reason */
            reason: string;
        };
        /** StepResponse */
        StepResponse: {
            /** Attempt Count */
            attempt_count: number;
            /** Name */
            name: string;
        };
        /** TokenResponse */
        TokenResponse: {
            /** Access Token */
            access_token: string;
            /** Expires In */
            expires_in: number;
            /**
             * Token Type
             * @default bearer
             * @constant
             */
            token_type: "bearer";
        };
        /** UserResponse */
        UserResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            role: components["schemas"]["Role"];
            /** Username */
            username: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    login_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TokenResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_auth_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserResponse"];
                };
            };
        };
    };
    list_companies_companies_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompanyListResponse"];
                };
            };
        };
    };
    get_company_companies__company_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                company_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompanyResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    list_investigations_investigations_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvestigationListResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_investigations_post: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvestigationCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvestigationResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detail_investigations__investigation_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                investigation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvestigationDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rerun_investigations__investigation_id__runs_post: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                investigation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RerunResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_report_reports__report_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                report_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReportResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pending_reviews_pending_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PendingFindingResponse"][];
                };
            };
        };
    };
    review_reviews__finding_id__post: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                finding_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_run_runs__run_id__review_post: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunReviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    workflow_run_events_workflow_runs__run_id__events_get: {
        parameters: {
            query?: never;
            header?: {
                "Last-Event-ID"?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
