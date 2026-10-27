/** Native mutation errors are structured; unrelated failures must not trigger draft recovery. */
export function isModelProfileConflict(error: unknown): boolean {
  return Boolean(error && typeof error === "object"
    && (error as { code?: unknown }).code === "model_configuration_conflict");
}
