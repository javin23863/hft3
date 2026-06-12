import { createContext, useContext } from "react";
import type { Zones } from "./types";

export const ZonesCtx = createContext<Zones>({});
export const useZones = () => useContext(ZonesCtx);
