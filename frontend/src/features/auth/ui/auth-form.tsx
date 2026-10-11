import { useState, type FormEvent } from "react";

import { Button } from "@/shared/ui/button";
import { Spinner } from "@/shared/ui/spinner";
import { TextField } from "@/shared/ui/text-field";

import { useAuthMutation } from "../api/auth";
import { authErrorMessage, MIN_PASSWORD_LENGTH } from "../lib/messages";

/** Форма входа или регистрации. Что делать после успеха, решает страница (onSuccess). */
export function AuthForm({
  mode,
  onSuccess,
}: {
  mode: "login" | "register";
  onSuccess: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const mutation = useAuthMutation(mode);
  const isRegister = mode === "register";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    if (!email.includes("@")) {
      setLocalError("Введите email");
      return;
    }
    if (isRegister && password.length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Пароль — не короче ${MIN_PASSWORD_LENGTH} символов`);
      return;
    }
    mutation.mutate({ email: email.trim(), password }, { onSuccess });
  };

  const error = localError ?? (mutation.isError ? authErrorMessage(mutation.error, mode) : null);

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <TextField
        label="Email"
        type="email"
        inputMode="email"
        autoComplete="email"
        placeholder="name@example.com"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        required
      />
      <TextField
        label="Пароль"
        type="password"
        autoComplete={isRegister ? "new-password" : "current-password"}
        hint={isRegister ? `Не короче ${MIN_PASSWORD_LENGTH} символов` : undefined}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        required
      />
      {error && (
        <p role="alert" className="px-1 text-[15px] text-danger">
          {error}
        </p>
      )}
      <Button type="submit" size="block" disabled={mutation.isPending}>
        {mutation.isPending ? (
          <Spinner className="text-brand-foreground" label="Отправляем" />
        ) : isRegister ? (
          "Создать аккаунт"
        ) : (
          "Войти"
        )}
      </Button>
    </form>
  );
}
