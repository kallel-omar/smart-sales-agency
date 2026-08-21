import { Link } from "react-router-dom";

import { useAppExperience } from "../../app/AppExperience";
import { Card } from "../ui/Card";

export function AuthPage({ title, description, children, footer }: { title: string; description: string; children: React.ReactNode; footer: React.ReactNode }) {
  const { t } = useAppExperience();
  return (
    <main id="main-content" className="public-auth-page">
      <div className="public-auth-intro"><Link to="/" className="public-brand"><img src="/hiri-logo.svg" alt="" /><span>HIRI</span></Link><p>{t("authFoundation")}</p><h1>{t("authStatement")}</h1></div>
      <Card className="public-auth-card"><p className="public-eyebrow">HIRI</p><h2>{title}</h2><p className="public-auth-description">{description}</p>{children}<div className="public-auth-footer">{footer}</div></Card>
    </main>
  );
}
