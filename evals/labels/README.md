# Human labels (P6.2). Written by a person, never generated.

`hotpot_correctness.csv` calibrates the answer judge. Get the pool from the eval-gate run's
artifact (`results/gate/label_pool.csv`: qid, question, gold, answer, human_correct, note), fill
`human_correct` with 1 (the answer states the same fact as the gold) or 0, and commit it here.
Ignore [bracketed] citations; a paraphrase or extra precision is correct; hedging between two
options or a different entity is not. The pool shows neither F1 nor the judge's verdict, so
neither anchors you. Blank rows are skipped. The judge gates only at kappa >= 0.6 on >= 100 rows.
