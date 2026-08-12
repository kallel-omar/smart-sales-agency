import { Construction } from "lucide-react";

import { EmptyState } from "../components/ui/EmptyState";
import { PageHeader } from "../components/ui/PageHeader";

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <PageHeader
        eyebrow="Foundation"
        title={title}
        description="This route is wired into the dashboard shell. Full workflow functionality is intentionally deferred beyond Task 301."
      />
      <div className="p-5 sm:p-7">
        <EmptyState
          icon={Construction}
          title={`${title} foundation is ready`}
          description="Navigation, authentication, workspace scope, loading, and safe empty states are in place for the next frontend tasks."
        />
      </div>
    </div>
  );
}
