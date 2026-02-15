import { Router } from "express";
import { getAlertConfig, setAlertConfig } from "../config.js";
import { AlertConfigSchema } from "@kalshi-monitor/shared";
import { WEIGHT_PRESETS } from "@kalshi-monitor/shared";
import { getIngestionStats } from "../services/ingestion.js";

export const configRouter = Router();

/**
 * GET /api/config
 * Current scoring weights and alert config.
 */
configRouter.get("/", (_req, res) => {
  res.json({
    config: getAlertConfig(),
    presets: Object.keys(WEIGHT_PRESETS),
  });
});

/**
 * PUT /api/config
 * Update scoring weights and thresholds.
 */
configRouter.put("/", (req, res) => {
  try {
    const body = req.body;

    // If a preset name is provided, use it
    if (body.preset && WEIGHT_PRESETS[body.preset]) {
      const updated = setAlertConfig({
        weights: WEIGHT_PRESETS[body.preset],
      });
      return res.json({ config: updated, applied_preset: body.preset });
    }

    // Otherwise validate and apply partial update
    const partial = AlertConfigSchema.partial().safeParse(body);
    if (!partial.success) {
      return res.status(400).json({ error: "Invalid config", details: partial.error.issues });
    }

    const updated = setAlertConfig(partial.data);
    res.json({ config: updated });
  } catch (err) {
    res.status(500).json({ error: "Failed to update config" });
  }
});

/**
 * GET /api/stats
 * Ingestion and system stats.
 */
configRouter.get("/stats", (_req, res) => {
  res.json(getIngestionStats());
});
