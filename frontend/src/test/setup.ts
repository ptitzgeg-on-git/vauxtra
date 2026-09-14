/**
 * Two budgets, stated here rather than left at their defaults.
 *
 * `asyncUtilTimeout` is what every `findBy*` and every bare `waitFor` in this suite gets,
 * and the default is 1000 ms. That number was chosen for a DOM assertion; the components
 * here mount a query client and wait on reads, and the first test in a file also pays the
 * run's one-off warm-up. Measured on an idle eight-core machine, the heaviest of them --
 * the happy path of `TemplateModal.test.tsx`, which waits for two reads to land -- takes
 * 573 ms in first position and 241 ms anywhere else, against 60 to 170 ms for every other
 * test in that file. So the default left that one test 1.7 times its own cost in headroom
 * while everything around it had ten, and it went red under the full suite on a machine
 * that was busy. The same-shaped first test of `Templates.test.tsx` went with it. CI runs
 * on a runner with half the cores of the machine those figures came from.
 *
 * A budget only ever elapses on a test that is failing anyway, so a longer one costs
 * nothing on green and buys back the margin that makes a red build a signal rather than a
 * coin toss. `testTimeout` in `vitest.config.ts` is raised alongside it, because a test
 * that waits three times in a row has to be allowed to spend three of these.
 */

import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
});
