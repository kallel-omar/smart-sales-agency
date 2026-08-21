import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { useAuth } from "../auth/AuthProvider";
import { ApiError } from "../lib/api";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { AppExperienceProvider, useAppExperience } from "../app/AppExperience";

type LoginFormValues = { email: string; password: string };

export function LoginPage() {
  return <AppExperienceProvider><LocalizedLoginPage /></AppExperienceProvider>;
}

function LocalizedLoginPage() {
  const { direction, locale, t } = useAppExperience();
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const from = typeof location.state === "object" && location.state ? location.state.from : "/app";
  const loginSchema = z.object({
    email: z.string().min(1, t("emailRequired")).email(t("validEmail")),
    password: z.string().min(1, t("passwordRequired"))
  });

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting }
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" }
  });

  if (auth.status === "authenticated") {
    return <Navigate to="/app" replace />;
  }

  const onSubmit = async (values: LoginFormValues) => {
    setSubmitError(null);
    try {
      await auth.login(values.email, values.password);
      navigate(typeof from === "string" ? from : "/app", { replace: true });
    } catch (error) {
      setSubmitError(error instanceof ApiError && error.status === 401 ? t("invalidCredentials") : t("unableSignIn"));
    }
  };

  return (
    <div className="grid min-h-screen bg-[#07101f] lg:grid-cols-[0.9fr_1.1fr]" dir={direction} lang={locale}>
      <div className="relative hidden overflow-hidden border-r border-white/10 p-12 text-white lg:flex lg:flex-col lg:justify-between"><div className="hiri-mark-geometry absolute -bottom-32 -left-40 h-[36rem] w-[36rem] opacity-20" aria-hidden="true" /><div className="relative flex items-center gap-3"><img className="h-9 w-9" src="/hiri-logo.svg" alt="HIRI logo" /><span className="text-lg font-bold tracking-[0.2em]">HIRI</span></div><div className="relative max-w-xl"><p className="text-sm font-semibold text-blue-300">{t("operatingSystem")}</p><p className="mt-6 text-5xl font-semibold leading-[1.05] tracking-[-0.045em]">{t("operateConfidence")}</p><p className="mt-6 max-w-lg text-lg leading-8 text-slate-400">{t("structuredWork")}</p></div><p className="relative text-xs text-slate-600">HIRI · {t("humanGovernedAiOperations")}</p></div>
      <div className="flex items-center justify-center bg-slate-50 px-5 py-12">
      <Card className="w-full max-w-md p-7 shadow-soft sm:p-9">
        <div className="mb-7"><div className="mb-8 flex items-center gap-3 lg:hidden"><img className="h-8 w-8" src="/hiri-logo.svg" alt="HIRI logo" /><span className="font-bold tracking-[0.2em] text-slate-950">HIRI</span></div><p className="text-sm font-semibold text-brand-600">{t("operatorAccess")}</p><h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em] text-slate-950">{t("signInWorkspace")}</h1><p className="mt-3 text-sm leading-6 text-slate-600">{t("accessEnvironment")}</p></div>

        <form className="space-y-4" onSubmit={handleSubmit(onSubmit)}>
          <Input
            label={t("email")}
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <Input
            label={t("password")}
            type="password"
            autoComplete="current-password"
            error={errors.password?.message}
            {...register("password")}
          />
          {submitError ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {submitError}
            </div>
          ) : null}
          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? t("signingIn") : t("signIn")}
          </Button>
        </form>
      </Card></div>
    </div>
  );
}
