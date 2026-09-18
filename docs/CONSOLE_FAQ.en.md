# RootSleuth Operations Console FAQ

English | [简体中文](CONSOLE_FAQ.md)

**Q1: Why can't I approve my own request?**
The system enforces self-approval prevention (returns 403 when the applicant == the current operator). Changes must be independently reviewed by another role at the same level or higher, ensuring traceability and checks and balances.

**Q2: Do I still need to make up a case ID when creating a new case?**
No. After submission the backend automatically generates a unique ID in the `KB-YYYYMMDD-NNN` format: the date is the current UTC day, and the sequence number takes the smallest free slot after de-duplicating against "stored cases + in-flight creation change sets", so repeated submissions while an approval is in flight never conflict. The ID is visible in both the creation success toast and the approval request content.

**Q3: When does knowledge take effect after approval?**
Final approval immediately writes a new version snapshot and invalidates the semantic cache (audit action `semantic_cache_invalidate`); the pipeline uses the new knowledge on its next retrieval — no service restart needed.

**Q4: What should I do when AI diagnosis shows "degraded"?**
Check the degradation reason in the detail view:
- `llm_timeout`: increase `llm_timeout_seconds` in the configuration center (default 45, cap 300).
- `invalid_json`: the model output was occasionally invalid; click diagnose again.
- RAG returned no candidates (unknown): consider distilling this alert class into a new knowledge case.

**Q5: Where do rejected changes go?**
The change set status is set to `rejected` and its content is not published; you can view its diff and rationale under the knowledge base "change records", modify it, and resubmit.

**Q6: Does rollback lose historical versions?**
No. Knowledge case rollback reads the target version snapshot and generates a new version; rule rollback reactivates the target version and marks the currently active version as superseded. The full history remains traceable.

**Q7: How do I switch approval modes?**
sys_admin changes `approval_mode` in "Audit & Config → Configuration Center" (OFF / SINGLE_REVIEW / MULTI_LEVEL); it takes effect immediately and is written to the audit log. In-flight approval requests continue flowing with the number of steps set at their creation time.

**Q8: Why can't I see a request in "Pending My Approval"?**
Common reasons: ① you are the applicant of that request (self-approval is forbidden); ② the role required by the current approval step is higher than your role; ③ the step has already been processed or the request is no longer in a pending state.

**Q9: What score do rules promoted from unknown templates get?**
Fixed `score: 0.6` and `severity: warning`, with keywords taken from English tokens in the template (up to 6). After promotion you can adjust them in rule management and republish.

**Q10: What demo accounts are available in preview environments?**
- `demo-operator@atoms.dev` → On-call operator (can diagnose, give feedback)
- `demo-sre@atoms.dev` → SRE (can edit the knowledge base, initiate approvals)
- `demo-lead@atoms.dev` → Approver (can approve)
- `demo-admin@atoms.dev` → System administrator (rule publishing, configuration center)

**Q11: In a merge proposal, what is the relationship between the "primary case" and "redundant cases"?**
After the merge, the primary case is kept and continues receiving feedback, while redundant cases are archived (`archived`, no longer participating in RAG recall). The system defaults to recommending the case with the highest feedback score as the primary case.

**Q12: How is the "risk level" on an approval request determined?**
Modifying `root_cause` or `solution` in a knowledge base change makes it medium; anything else is low; merges and template promotions are fixed at medium.
