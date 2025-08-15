// Small helper to normalize filenames in the frontend to match backend NFC normalization
export const normalizeFileName = (s?: string | null) => {
  if (!s) {
    return s as any;
  }
  try {
    // Use Unicode NFC normalization and trim surrounding whitespace
    // This mirrors backend normalize_unicode_text + normalize_file_name behaviour
    return (s as string).normalize('NFC').trim();
  } catch (e) {
    return s as any;
  }
};

export default normalizeFileName;
