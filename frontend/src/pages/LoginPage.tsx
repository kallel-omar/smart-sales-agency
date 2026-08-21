import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { useAppExperience } from "../app/AppExperience";
import { useAuth } from "../auth/AuthProvider";
import { AuthPage } from "../components/auth/AuthPage";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { ApiError } from "../lib/api";

type LoginFormValues = { email: string; password: string };

export function LoginPage() {
  const { t } = useAppExperience();
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const from = typeof location.state === "object" && location.state ? location.state.from : "/app";
  const schema = z.object({ email: z.string().min(1, t("emailRequired")).email(t("validEmail")), password: z.string().min(1, t("passwordRequired")) });
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginFormValues>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  if (auth.status === "authenticated") return <Navigate to="/app" replace />;

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
    <AuthPage title={t("signInWorkspace")} description={t("accessEnvironment")} footer={<><span>{t("needAccount")}</span> <Link to="/register">{t("createAccount")}</Link></>}>
      <form className="public-auth-form" onSubmit={handleSubmit(onSubmit)}>
        <Input label={t("email")} type="email" autoComplete="email" error={errors.email?.message} {...register("email")} />
        <Input label={t("password")} type="password" autoComplete="current-password" error={errors.password?.message} {...register("password")} />
        {submitError ? <div className="public-form-error" role="alert">{submitError}</div> : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>{isSubmitting ? t("signingIn") : t("signIn")}</Button>
      </form>
    </AuthPage>
  );
}
