import { z } from "zod";

export const ScoringWeightsSchema = z.object({
  size_weight: z.number().min(0).max(1).default(0.25),
  late_weight: z.number().min(0).max(1).default(0.20),
  impact_weight: z.number().min(0).max(1).default(0.20),
  liquidity_weight: z.number().min(0).max(1).default(0.20),
  persistence_weight: z.number().min(0).max(1).default(0.15),
});

export type ScoringWeights = z.infer<typeof ScoringWeightsSchema>;

export const AlertConfigSchema = z.object({
  score_threshold: z.number().min(0).max(100).default(40),
  cooldown_seconds: z.number().min(0).default(120),
  cooldown_score_delta: z.number().min(0).default(15),
  min_open_interest: z.number().min(0).default(50),
  weights: ScoringWeightsSchema,
});

export type AlertConfig = z.infer<typeof AlertConfigSchema>;

export const DEFAULT_WEIGHTS: ScoringWeights = {
  size_weight: 0.25,
  late_weight: 0.20,
  impact_weight: 0.20,
  liquidity_weight: 0.20,
  persistence_weight: 0.15,
};

export const DEFAULT_CONFIG: AlertConfig = {
  score_threshold: 40,
  cooldown_seconds: 120,
  cooldown_score_delta: 15,
  min_open_interest: 50,
  weights: DEFAULT_WEIGHTS,
};
