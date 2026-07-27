# What We Learned

## The answer: do not change the model recommendations yet

This experiment tested whether the application's current way of translating a
model's capability score into a likelihood of success should be changed. The
answer is no - not because the models performed identically, but because the new
shape was not reliable enough on unfamiliar models and unfamiliar kinds of work.

The existing settings remain in place. In practical terms, the app should keep
using the same relationship between a model's published capability score and its
estimated chance of completing a task well. It should also keep the current
settings for how sharply task difficulty changes that estimate.

## What the evidence says

Across the full set of evaluated answers, a reviewer judged roughly two-thirds
to be correct. That is useful evidence that the models differ in meaningful ways,
but it is not enough to justify redrawing the curve used in recommendations. A
proposed change needs to work not only on the examples used to discover it, but
also on questions and models it has not seen before. This one did not clear that
test consistently.

We also checked the reviewer against 200 public answers that had each been
scored independently by three people. The reviewer reached the same conclusion
as the human assessments about 84% of the time. That is promising, but below the
standard needed to let an automated reviewer change product recommendations on
its own. It was especially cautious about answers that people considered partly
or mostly correct.

## Takeaways

- Higher-capability models still tend to be more useful, but this experiment did not provide a dependable reason to change how much extra value the app assigns to each step up in capability.
- The current recommendations are more trustworthy because the system kept the existing settings when the new evidence was mixed.
- AI review is helpful for examining many answers, but it is not a substitute for people when the result would change a user-facing recommendation.
- The next useful evidence is more human-scored work across a wider range of tasks, especially research, legal, financial, and coding work - not simply more automated judging of the same kind of answer.

## Bottom line

The experiment strengthened the case for restraint. The application has enough
evidence to continue using its current calibration, but not enough to claim a
better one. That is a valuable result: it prevents a plausible-looking change
from making recommendations less reliable.
