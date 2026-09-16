"""The scheduler, over a life rather than a single answer.

Every other suite answers a card once or twice. This one walks one card through
ten perfect reviews and then a failure, because a wiring mistake — the wrong
clock passed in, a state not carried across — looks completely normal for one
answer and only shows up as intervals that do not move.
"""


def run(t):
    t.open_app()
    out = t.page.evaluate("""async () => {
      const { scheduler, answer, preview, Rating } = await import('./review.js');
      const engine = scheduler(0.9);
      let card = { id: 'x', deck: 'd', word: 'x', clue: '', example: '',
        fsrs: { due: new Date().toISOString(), stability: 0, difficulty: 0, elapsed_days: 0,
                scheduled_days: 0, learning_steps: 0, reps: 0, lapses: 0, state: 0, last_review: null } };

      // Answered Good every time, exactly when it is owed.
      let now = new Date('2026-09-16T10:00:00Z');
      const intervals = [];
      for (let i = 0; i < 10; i += 1) {
        card = answer(engine, card, Rating.Good, now).card;
        intervals.push(card.fsrs.scheduled_days);
        now = new Date(card.fsrs.due);
      }

      const settled = card;
      const lapsed = answer(engine, card, Rating.Again, now).card;
      const labels = preview(engine, settled, now);
      return {
        intervals,
        grow: intervals.every((v, i) => i === 0 || v >= intervals[i - 1]),
        endsInReview: settled.fsrs.state === 2,
        stability: Math.round(settled.fsrs.stability),
        reps: settled.fsrs.reps,
        collapsed: lapsed.fsrs.scheduled_days < settled.fsrs.scheduled_days,
        lapses: lapsed.fsrs.lapses,
        relearning: lapsed.fsrs.state === 3,
        labelled: [1, 2, 3, 4].every(r => typeof labels[r] === "string" && labels[r].length > 0),
      };
    }""")

    t.note("ten Goods, in days", " ".join(str(v) for v in out["intervals"]))
    t.check("each interval is at least as long as the one before", out["grow"], True)
    t.check("ten answers are ten reps", out["reps"], 10)
    t.check("and the card ends up settled rather than still learning",
            out["endsInReview"], True)
    t.truthy(f"with a stability to match ({out['stability']} days)", out["stability"] > 100)

    t.check("then one Again collapses the interval", out["collapsed"], True)
    t.check("counts the lapse", out["lapses"], 1)
    t.check("and puts it back into relearning", out["relearning"], True)

    t.check("and every grade button says what it would cost", out["labelled"], True)
