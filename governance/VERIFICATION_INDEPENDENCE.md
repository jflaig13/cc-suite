# Verification independence

The verifier must not share the implementation's parser, calculations, prompt, derived fixture or reasoning as its proof of correctness. The builder's artifacts are the subject being tested, not an independent source of expected truth.

For each verification, identify the original source, the expected result, the extraction method, the comparator and their dependencies. Look for shared failure modes. Two computations over the same incorrect input do not validate that input.

For consequential data, establish an independent authoritative basis. Reconcile identity, position, units, effective time, exclusions and totals per relevant unit. Preserve the source revision used; do not patch source data to make the result match.

Run the original reproduction and relevant neighboring behavior on the exact deployed or production-like version required by the task. Refresh cached or stale state when freshness matters. Record what was observed and what could not be exercised.

An independent reviewer may request repairs but does not silently modify the subject it is certifying. Repairs create a new subject and require affected checks again. Verification itself must have a failure path: restore a known defect or supply adversarial input and confirm that the verifier rejects it.
