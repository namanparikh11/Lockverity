import { Link } from "react-router";

import type { ExternalEvidenceProviderSelection } from "@/api/types";

export function ExternalEvidenceProviderSelector({
  value,
  onChange,
  openssfApplicable = true,
  disabled = false,
  idPrefix,
}: {
  value: ExternalEvidenceProviderSelection;
  onChange: (value: ExternalEvidenceProviderSelection) => void;
  openssfApplicable?: boolean;
  disabled?: boolean;
  idPrefix: string;
}) {
  function setProvider(
    provider: keyof ExternalEvidenceProviderSelection,
    enabled: boolean
  ) {
    onChange({ ...value, [provider]: enabled });
  }

  return (
    <fieldset className="rounded-md border border-ink-200 bg-ink-50 p-3">
      <legend className="px-1 text-sm font-semibold text-ink-900">
        External evidence providers
      </legend>
      <div className="space-y-3">
        <ProviderChoice
          id={`${idPrefix}-osv`}
          label="OSV"
          description="Vulnerability evidence"
          checked={value.osv}
          disabled={disabled}
          onChange={(checked) => setProvider("osv", checked)}
        />
        <ProviderChoice
          id={`${idPrefix}-deps-dev`}
          label="deps.dev"
          description="Package/dependency metadata"
          checked={value.deps_dev}
          disabled={disabled}
          onChange={(checked) => setProvider("deps_dev", checked)}
        />
        <ProviderChoice
          id={`${idPrefix}-openssf`}
          label="OpenSSF Scorecard"
          description={
            openssfApplicable
              ? "Repository posture — GitHub repositories only"
              : "Not applicable to archive uploads"
          }
          checked={value.openssf}
          disabled={disabled || !openssfApplicable}
          onChange={(checked) => setProvider("openssf", checked)}
        />
      </div>
      <p className="mt-3 text-xs text-ink-600">
        Running this scan sends the documented repository/package coordinates
        to the selected providers. {" "}
        <Link to="/privacy" className="link">
          Privacy policy
        </Link>
      </p>
    </fieldset>
  );
}

function ProviderChoice({
  id,
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label htmlFor={id} className="flex items-start gap-2 text-sm text-ink-800">
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 h-4 w-4 rounded border-ink-300 text-accent-700"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <span className="font-medium text-ink-900">{label}</span>
        <span className="block text-xs text-ink-500">{description}</span>
      </span>
    </label>
  );
}
