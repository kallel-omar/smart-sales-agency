import { zodResolver } from "@hookform/resolvers/zod";
import { LockKeyhole } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { useAuth } from "../auth/AuthProvider";
import { ApiError } from "../lib/api";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";

const loginSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: z.string().min(1, "Password is required")
});

type LoginFormValues = z.infer<typeof loginSchema>;

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const from = typeof location.state === "object" && location.state ? location.state.from : "/app";

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
      setSubmitError(error instanceof ApiError && error.status === 401 ? "Invalid credentials" : "Unable to sign in");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-10">
      <Card className="w-full max-w-md p-6 shadow-soft">
        <div className="mb-6">
          <div className="mb-5 inline-flex rounded-lg bg-slate-950 p-3 text-white">
            <LockKeyhole aria-hidden="true" className="h-6 w-6" />
          </div>
          <p className="text-sm font-semibold uppercase tracking-wide text-brand-600">HIRI</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">Sign in to your workspace</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Access the Smart Sales Agency operating dashboard.
          </p>
        </div>

        <form className="space-y-4" onSubmit={handleSubmit(onSubmit)}>
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <Input
            label="Password"
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
            {isSubmitting ? "Signing in" : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
