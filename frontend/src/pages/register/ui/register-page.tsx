import { Link, useNavigate } from "@tanstack/react-router";

import { AuthForm } from "@/features/auth";
import { AuthLayout } from "@/widgets/app-shell";
import { LargeTitle } from "@/shared/ui/large-title";

export function RegisterPage({ redirect }: { redirect?: string }) {
  const navigate = useNavigate();
  return (
    <AuthLayout>
      <LargeTitle subtitle="Пароли от музыкальных площадок нам не нужны — никогда">
        Регистрация
      </LargeTitle>
      <AuthForm
        mode="register"
        onSuccess={() => navigate({ to: redirect ?? "/connections", replace: true })}
      />
      <p className="text-center text-[15px] text-label-secondary">
        Уже есть аккаунт?{" "}
        <Link to="/login" search={{ redirect }} className="font-semibold text-brand">
          Войти
        </Link>
      </p>
    </AuthLayout>
  );
}
