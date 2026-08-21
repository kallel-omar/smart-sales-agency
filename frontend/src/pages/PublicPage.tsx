import { useAppExperience } from "../app/AppExperience";

const pageKeys = {
  platform: ["platformNav", "platformPageTitle", "platformPageDescription"],
  how: ["howItWorksNav", "howPageTitle", "howPageDescription"],
  sales: ["salesNav", "salesPageTitle", "salesPageDescription"],
  about: ["aboutNav", "aboutPageTitle", "aboutPageDescription"],
  contact: ["contactNav", "contactPageTitle", "contactPageDescription"]
} as const;

export function PublicPage({ page }: { page: keyof typeof pageKeys }) {
  const { t } = useAppExperience();
  const [label, title, description] = pageKeys[page];
  return (
    <main id="main-content" className="public-foundation-page">
      <div className="public-page-container">
        <p className="public-eyebrow">{t(label)}</p>
        <h1>{t(title)}</h1>
        <p className="public-page-lead">{t(description)}</p>
        <section className="public-foundation-note" aria-label={t("foundationLabel")}><h2>{t("foundationLabel")}</h2><p>{t("foundationNote")}</p></section>
      </div>
    </main>
  );
}
