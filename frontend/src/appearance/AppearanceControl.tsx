/*
 Compact radio-group control for System / Light / Dark.

 A ``role="radiogroup"`` of three ``role="radio"``
 buttons. The implementation uses native
 ``<input type="radio">`` controls under the styled
 label markup so screen readers announce the role,
 the checked state, and the accessible name
 correctly. Keyboard navigation (Tab into the group,
 arrow keys to change, Space/Enter to commit) is the
 browser default for grouped radios.

 A short description below the group explains what
 "System" does so a user who has never seen the
 control can choose without reading docs.
*/

import { useId } from "react";

import { useAppearance } from "./useAppearance";
import type { Appearance } from "./appearance";

interface Choice {
  value: Appearance;
  label: string;
  hint: string;
}

const CHOICES: Choice[] = [
  {
    value: "system",
    label: "System",
    hint: "Follows your operating system appearance.",
  },
  {
    value: "light",
    label: "Light",
    hint: "Always use the light theme.",
  },
  {
    value: "dark",
    label: "Dark",
    hint: "Always use the dark theme.",
  },
];

export interface AppearanceControlProps {
  /** Optional override id used in tests. */
  testId?: string;
}

export function AppearanceControl({
  testId = "appearance-control",
}: AppearanceControlProps) {
  const { appearance, setAppearance } = useAppearance();
  const groupId = useId();
  const groupLabelId = `${groupId}-label`;
  const groupDescId = `${groupId}-description`;

  return (
    <section
      aria-labelledby={groupLabelId}
      aria-describedby={groupDescId}
      data-testid={testId}
      className="rounded-lg border border-ink-200 bg-surface p-4 shadow-sm"
    >
      <h3
        id={groupLabelId}
        className="text-sm font-semibold text-ink-900"
      >
        Appearance
      </h3>
      <p
        id={groupDescId}
        className="mt-1 text-xs text-ink-500"
      >
        System follows your operating system appearance.
      </p>
      <div
        role="radiogroup"
        aria-label="Appearance"
        className="mt-3 inline-flex rounded-md border border-ink-200 bg-canvas p-0.5"
      >
        {CHOICES.map((choice) => {
          const checked = appearance === choice.value;
          const inputId = `${groupId}-${choice.value}`;
          return (
            <label
              key={choice.value}
              htmlFor={inputId}
              data-testid={`${testId}-option-${choice.value}`}
              data-checked={checked ? "true" : "false"}
              className={[
                "relative cursor-pointer select-none rounded px-3 py-1.5 text-xs font-medium transition",
                checked
                  ? "bg-surface text-accent-700 shadow-sm"
                  : "text-ink-600 hover:text-ink-900",
                "focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent-500",
              ].join(" ")}
            >
              <input
                id={inputId}
                type="radio"
                name="appearance"
                value={choice.value}
                checked={checked}
                onChange={() => setAppearance(choice.value)}
                className="sr-only"
                aria-describedby={`${inputId}-hint`}
                data-testid={`${testId}-input-${choice.value}`}
              />
              {choice.label}
              <span id={`${inputId}-hint`} className="sr-only">
                {choice.hint}
              </span>
            </label>
          );
        })}
      </div>
    </section>
  );
}
