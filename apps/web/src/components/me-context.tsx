"use client";
import { createContext, useContext } from "react";
import type { Me } from "@/lib/types";

export const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe dipakai di luar MeContext");
  return me;
}

export function useCan(permission: string): boolean {
  return useMe().permissions.includes(permission);
}
