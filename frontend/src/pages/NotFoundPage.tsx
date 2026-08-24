import { Link } from "react-router";

import { PageHeader } from "@/components/PageHeader";

export function NotFoundPage() {
  return (
    <>
      <PageHeader
        title="Page not found"
        description="The URL you followed does not match any Lockverity route."
      />
      <div className="card max-w-xl">
        <p className="text-sm text-ink-700">
          Try the{" "}
          <Link to="/" className="text-accent-700 hover:text-accent-800">
            dashboard
          </Link>{" "}
          or the{" "}
          <Link
            to="/repositories"
            className="text-accent-700 hover:text-accent-800"
          >
            repositories list
          </Link>
          .
        </p>
      </div>
    </>
  );
}
