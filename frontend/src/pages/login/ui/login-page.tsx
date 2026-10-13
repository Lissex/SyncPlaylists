import { Link, useNavigate } from "@tanstack/react-router";

import { AuthForm } from "@/features/auth";
import { AuthLayout } from "@/widgets/app-shell";
import { LargeTitle } from "@/shared/ui/large-title";

export function LoginPage({ redirect }: { redirect?: string }) {
  const navigate = useNavigate();
  return (
    <AuthLayout>
      <LargeTitle subtitle="Переносите плейлисты между площадками">Вход</LargeTitle>
      <AuthForm mode="login" onSuccess={() => navigate({ to: redirect ?? "/", replace: true })} />
      <p className="text-center text-[15px] text-label-secondary">
        Нет аккаунта?{" "}
        <Link to="/register" search={{ redirect }} className="font-semibold text-brand">
          Зарегистрироваться
        </Link>
      </p>
    </AuthLayout>
  );
}
