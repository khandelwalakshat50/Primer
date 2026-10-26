import { useEffect, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from "react";
import { Button, Field } from "../ui";

type Props = {
  disabled: boolean;
  onSend: (text: string, opts?: { source?: boolean }) => void;
};

export function ChatBar({ disabled, onSend }: Props) {
  const [value, setValue] = useState("");
  const [ownText, setOwnText] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const sending = useRef(false);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const cap = ownText ? 220 : 120;
    el.style.height = `${Math.min(el.scrollHeight, cap)}px`;
  }, [value, ownText]);

  function submit() {
    const text = value.trim();
    if (!text || disabled || sending.current) return;
    sending.current = true;
    onSend(text, ownText ? { source: true } : undefined);
    setValue("");
    setOwnText(false);
    window.setTimeout(() => {
      sending.current = false;
    }, 800);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
      return;
    }
    if (ownText) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const pasted = e.clipboardData.getData("text");
    const breaks = (pasted.match(/\n/g) || []).length;
    if (pasted.length > 400 || breaks >= 3) {
      setOwnText(true);
    }
  }

  return (
    <form className={`chatbar${ownText ? " is-source" : ""}`} onSubmit={onSubmit}>
      <label className="chatbar-label" htmlFor="learner-input">
        {ownText ? "Your text" : "Write to Primer"}
      </label>
      <div className="chatbar-row">
        <Field
          id="learner-input"
          ref={ref}
          rows={ownText ? 6 : 1}
          value={value}
          disabled={disabled}
          placeholder={
            ownText
              ? "Paste an article, notes, or a long explanation. Primer will turn the important parts into a course."
              : "Ask, answer, or say what you want to learn…"
          }
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <div className="chatbar-actions">
          <Button
            type="button"
            variant="ghost"
            className={ownText ? "is-on" : ""}
            disabled={disabled}
            aria-pressed={ownText}
            onClick={() => {
              setOwnText((on) => !on);
              window.setTimeout(() => ref.current?.focus(), 0);
            }}
          >
            My text
          </Button>
          <Button type="submit" disabled={disabled || !value.trim()}>
            {ownText ? "Make course" : "Ask"}
          </Button>
        </div>
      </div>
    </form>
  );
}
