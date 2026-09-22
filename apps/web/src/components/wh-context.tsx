"use client";
import { createContext, useContext } from "react";
import type { Warehouse } from "@/lib/types";

export const WhContext = createContext<Warehouse | null>(null);
export const useWarehouse = () => useContext(WhContext);
