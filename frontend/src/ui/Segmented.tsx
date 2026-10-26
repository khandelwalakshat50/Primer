type Option<T extends string> = {
  id: T;
  label: string;
  disabled?: boolean;
};

type Props<T extends string> = {
  value: T;
  options: Option<T>[];
  onChange: (id: T) => void;
  ariaLabel: string;
};

export function Segmented<T extends string>({ value, options, onChange, ariaLabel }: Props<T>) {
  return (
    <div className="seg" role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={value === option.id}
          className={value === option.id ? "is-on" : ""}
          disabled={option.disabled}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
