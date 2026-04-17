import { CircleHelp } from "lucide-react";

interface FieldHintProps {
  text: string;
}

export function FieldHint({ text }: FieldHintProps) {
  return (
    <span className="relative inline-flex items-center group" tabIndex={0}>
      <CircleHelp className="w-3.5 h-3.5 text-muted-foreground" />
      <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 -translate-x-1/2 whitespace-normal rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground shadow-md opacity-0 transition-opacity w-56 group-hover:opacity-100 group-focus-visible:opacity-100">
        {text}
      </span>
    </span>
  );
}
