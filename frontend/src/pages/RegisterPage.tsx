import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { z } from "zod";

import { useAppExperience } from "../app/AppExperience";
import { useAuth } from "../auth/AuthProvider";
import { AuthPage } from "../components/auth/AuthPage";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { ApiError, apiClient } from "../lib/api";

type RegisterValues = { displayName: string; email: string; password: string; confirmPassword: string };

export function RegisterPage() {
  const { t } = useAppExperience();
  const auth = useAuth();
  const navigate = useNavigate();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const schema = z.object({
    displayName: z.string().trim().min(1, t("displayNameRequired")),
    email: z.string().min(1, t("emailRequired")).email(t("validEmail")),
    password: z.string().min(12, t("passwordMinimum")),
    confirmPassword: z.string().min(1, t("passwordRequired"))
  }).refine((values) => values.password === values.confirmPassword, { path: ["confirmPassword"], message: t("passwordsMismatch") });
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<RegisterValues>({ resolver: zodResolver(schema), defaultValues: { displayName: "", email: "", password: "", confirmPassword: "" } });

  if (auth.status === "authenticated") return <Navigate to="/app" replace />;

  const onSubmit = async (values: RegisterValues) => {
    setSubmitError(null);
    try {
      await apiClient.register(values.email, values.password, values.displayName);
      await auth.login(values.email, values.password);
      navigate("/app", { replace: true });
    } catch (error) {
      setSubmitError(error instanceof ApiError && error.status === 409 ? t("accountExists") : t("unableCreateAccount"));
    }
  };

  return (
    <AuthPage title={t("registerTitle")} description={t("registerDescription")} footer={<><span>{t("alreadyHaveAccount")}</span> <Link to="/login">{t("loginAction")}</Link></>}>
      <form className="public-auth-form" onSubmit={handleSubmit(onSubmit)}>
        <Input label={t("displayName")} autoComplete="name" error={errors.displayName?.message} {...register("displayName")} />
        <Input label={t("email")} type="email" autoComplete="email" error={errors.email?.message} {...register("email")} />
        <Input label={t("password")} type="password" autoComplete="new-password" error={errors.password?.message} {...register("password")} />
        <Input label={t("confirmPassword")} type="password" autoComplete="new-password" error={errors.confirmPassword?.message} {...register("confirmPassword")} />
        {submitError ? <div className="public-form-error" role="alert">{submitError}</div> : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>{isSubmitting ? t("creatingAccount") : t("createAccount")}</Button>
      </form>
    </AuthPage>
  );
}
