import { useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Телефон или планшет без мыши — расширение там не работает. */
export function useIsTouchDevice(): boolean {
  return useMediaQuery("(pointer: coarse) and (hover: none)");
}
