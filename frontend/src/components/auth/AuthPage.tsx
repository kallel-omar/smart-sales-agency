import { Link } from "react-router-dom";

import { useAppExperience } from "../../app/AppExperience";
import { usePublicContent } from "../../app/publicContent";
import { Card } from "../ui/Card";

export function AuthPage({ title, description, children, footer }: { title: string; description: string; children: React.ReactNode; footer: React.ReactNode }) {
  const { t } = useAppExperience();
  const copy = usePublicContent();
  return (
    <main id="main-content" className="public-auth-page">
      <div className="public-auth-intro"><Link to="/" className="public-brand"><img src="/hiri-logo.svg" alt="" /><span>HIRI</span></Link><p>{t("authFoundation")}</p><h2>{t("authStatement")}</h2><div className="public-auth-controls">{copy.home.controls.slice(0, 3).map(([name, detail]) => <div key={name}><strong>{name}</strong><span>{detail}</span></div>)}</div></div>
      <Card className="public-auth-card"><p className="public-eyebrow">HIRI</p><h1>{title}</h1><p className="public-auth-description">{description}</p>{children}<div className="public-auth-footer">{footer}</div></Card>
    </main>
  );
}
