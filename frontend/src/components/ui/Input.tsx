import { forwardRef, type InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className = "", error, id, label, ...props }, ref) => {
    const inputId = id ?? props.name;
    return (
      <label className="block text-sm font-medium text-slate-700" htmlFor={inputId}>
        <span>{label}</span>
        <input
          ref={ref}
          id={inputId}
          aria-invalid={Boolean(error)}
          className={`mt-2 block min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-950 shadow-sm transition placeholder:text-slate-400 focus:border-brand-500 ${className}`}
          {...props}
        />
        {error ? <span className="mt-2 block text-sm text-red-600">{error}</span> : null}
      </label>
    );
  }
);

Input.displayName = "Input";
