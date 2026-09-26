export interface ThumbnailSizes {
  small: number;
  medium: number;
  large: number;
}

export const THUMBNAIL_SIZES: ThumbnailSizes = {
  small: 150,
  medium: 300,
  large: 500,
};

export function getSizesAttribute(maxWidth?: string): string {
  // Default responsive sizes - adjust based on your grid layout
  if (maxWidth) {
    return `(max-width: 768px) 100vw, ${maxWidth}`;
  }
  return '(max-width: 768px) 100vw, (max-width: 1024px) 50vw, 33vw';
}

// New CAS URL utilities

/**
 * Appends a thumbnail size parameter to a CAS URL.
 * Checks if the URL already contains a query string to use the correct separator.
 * @param baseUrl - The base CAS URL (may be null or empty)
 * @param size - The thumbnail size in pixels
 * @returns The URL with thumbnail parameter appended, or null if baseUrl is null/empty
 */
export function appendThumbnailParam(baseUrl: string | null, size: number): string | null {
  if (!baseUrl) {
    return null;
  }
  const separator = baseUrl.includes('?') ? '&' : '?';
  return `${baseUrl}${separator}thumbnail=${size}`;
}

/**
 * Generates a srcSet string from a base CAS URL by appending thumbnail parameters for each size.
 * @param baseUrl - The base CAS URL (may be null or empty)
 * @returns A srcSet string for responsive images, or empty string if baseUrl is null/empty
 */
export function generateSrcSetFromUrl(baseUrl: string | null): string {
  if (!baseUrl) {
    return '';
  }
  const sizes = Object.entries(THUMBNAIL_SIZES);
  return sizes
    .map(([, size]) => {
      const url = appendThumbnailParam(baseUrl, size);
      return `${url} ${size}w`;
    })
    .join(', ');
}

