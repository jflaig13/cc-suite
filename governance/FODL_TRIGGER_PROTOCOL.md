# Handling the human decision queue

When the human asks to view decisions, refresh the authoritative queue and the source evidence first. Match any configured command as a whole prompt or token; a word quoted inside an unrelated message does not trigger an action.

Show only genuinely unresolved decisions. Use plain language, explain identifiers, and give the consequence of each option. Prefer a concise structured question where the host supports one. The host adapter must state what input tool is actually available.

Keep execution status separate from decisions awaiting the human. Do not present an action already authorized as a new approval. A response outside the offered options is feedback: explain the missing context instead of repeatedly presenting the same question.

Record the user's actual answer, including qualifications. Verify delegated authority if the answer arrived through a relay. Apply the decision across the canonical work item and its material consumers, then record the observed result. Failed execution stays visible and resumes from the failed dependency.

A missing answer is not permission. Continue independent authorized work while a required answer is pending. Respect an explicit pause or cancellation.

The queue is complete only when all relevant open decision-bearing sources reconcile with its entries. The UI representation is a projection of that authoritative record.
