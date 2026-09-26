/**
 * Generic hook for fetching all items from a paginated API endpoint.
 *
 * Fetches all pages sequentially (1000 items per page) until the backend returns
 * fewer than 1000 items, indicating the last page. The component receives the
 * complete dataset after all pages are loaded.
 *
 * Note: Refetches on every mount. Navigate away and back to refresh the list.
 */

import { useEffect, useState } from 'react';

const PAGINATION_LIMIT = 1000;

export interface UsePaginatedFetchAllResult<T> {
  data: T[];
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
  pagesFetched: number;
}

/**
 * Callback to fetch a single page of data.
 * @param offset - Number of items to skip
 * @param limit - Maximum number of items to return
 * @param signal - AbortSignal for cancellation
 * @returns Array of items for this page
 */
export type FetchPageCallback<T> = (
  offset: number,
  limit: number,
  signal: AbortSignal
) => Promise<T[]>;

/**
 * Optional callback to validate page data after fetching.
 * Throw an error to abort the fetch with a descriptive message.
 * @param pageData - The fetched page data
 * @param pageNumber - 1-based page number
 */
export type ValidatePageCallback<T> = (pageData: T[], pageNumber: number) => void;

export interface UsePaginatedFetchAllOptions<T> {
  /** Optional validation callback run on each page */
  validate?: ValidatePageCallback<T>;
}

/**
 * Fetches all items from a paginated endpoint automatically.
 * Returns a unified query state matching useQuery interface for component compatibility.
 *
 * @param fetchPage - Callback to fetch a single page
 * @param options - Optional configuration including validation callback
 */
export function usePaginatedFetchAll<T>(
  fetchPage: FetchPageCallback<T>,
  options?: UsePaginatedFetchAllOptions<T>
): UsePaginatedFetchAllResult<T> {
  const [data, setData] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [pagesFetched, setPagesFetched] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function fetchAllPages() {
      setIsLoading(true);
      setError(null);

      const allItems: T[] = [];
      let offset = 0;
      let pages = 0;

      try {
        while (true) {
          const pageData = await fetchPage(offset, PAGINATION_LIMIT, controller.signal);
          pages += 1;

          // Run optional validation
          if (options?.validate) {
            options.validate(pageData, pages);
          }

          allItems.push(...pageData);

          // Last page has fewer items than the limit
          if (pageData.length < PAGINATION_LIMIT) break;

          offset += PAGINATION_LIMIT;
        }

        if (!cancelled) {
          setData(allItems);
          setPagesFetched(pages);
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
          setIsLoading(false);
        }
      }
    }

    fetchAllPages();

    return () => {
      cancelled = true;
      controller.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchPage should be stable via useCallback
  }, [fetchPage]);

  return {
    data,
    isLoading,
    isFetching: isLoading,
    error,
    pagesFetched,
  };
}
