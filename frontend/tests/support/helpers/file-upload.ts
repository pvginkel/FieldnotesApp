/// <reference lib="dom" />
import { Page, Locator } from '@playwright/test';

/**
 * File upload configuration
 */
export interface FileUploadConfig {
  fileName: string;
  mimeType: string;
  content?: string | Buffer;
  size?: number;
}

/**
 * Helper class for file upload operations
 */
export class FileUploadHelper {
  constructor(private readonly page: Page) {}

  /**
   * Creates a mock file for upload
   * @param config - File configuration
   * @returns File path for upload
   */
  async createMockFile(config: FileUploadConfig): Promise<string> {
    // In memory file creation for testing

    // Set file content
    let content: string | Buffer;
    if (config.content) {
      content = config.content;
    } else {
      // Generate default content based on type
      content = this.generateDefaultContent(config.mimeType, config.size);
    }

    // For Playwright, we can use setInputFiles with buffer
    // This returns a data URI that can be used
    if (typeof content === 'string') {
      return `data:${config.mimeType};base64,${Buffer.from(content).toString('base64')}`;
    }

    return `data:${config.mimeType};base64,${content.toString('base64')}`;
  }

  /**
   * Uploads a file to a file input
   * @param selector - Selector for the file input
   * @param config - File configuration
   */
  async uploadFile(selector: string | Locator, config: FileUploadConfig): Promise<void> {
    const fileInput = typeof selector === 'string' ? this.page.locator(selector) : selector;

    // Create file buffer
    const buffer = config.content
      ? Buffer.from(config.content)
      : Buffer.from(this.generateDefaultContent(config.mimeType, config.size));

    await fileInput.setInputFiles({
      name: config.fileName,
      mimeType: config.mimeType,
      buffer,
    });
  }

  /**
   * Uploads multiple files
   * @param selector - Selector for the file input
   * @param configs - Array of file configurations
   */
  async uploadMultipleFiles(
    selector: string | Locator,
    configs: FileUploadConfig[]
  ): Promise<void> {
    const fileInput = typeof selector === 'string' ? this.page.locator(selector) : selector;

    const files = configs.map(config => ({
      name: config.fileName,
      mimeType: config.mimeType,
      buffer: config.content
        ? Buffer.from(config.content)
        : Buffer.from(this.generateDefaultContent(config.mimeType, config.size)),
    }));

    await fileInput.setInputFiles(files);
  }

  /**
   * Simulates drag and drop file upload
   * @param dropZoneSelector - Selector for the drop zone
   * @param config - File configuration
   */
  async dragAndDropFile(dropZoneSelector: string, config: FileUploadConfig): Promise<void> {
    // Create a data transfer with file
    await this.page.evaluate(
      ({ selector, fileName, mimeType, content }) => {
        const dropZone = document.querySelector(selector);
        if (!dropZone) {
          throw new Error(`Drop zone not found: ${selector}`);
        }

        // Create file
        const file = new File([content || ''], fileName, { type: mimeType });

        // Create data transfer
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);

        // Create and dispatch drag events
        const dragEnter = new DragEvent('dragenter', {
          dataTransfer,
          bubbles: true,
          cancelable: true,
        });

        const dragOver = new DragEvent('dragover', {
          dataTransfer,
          bubbles: true,
          cancelable: true,
        });

        const drop = new DragEvent('drop', {
          dataTransfer,
          bubbles: true,
          cancelable: true,
        });

        dropZone.dispatchEvent(dragEnter);
        dropZone.dispatchEvent(dragOver);
        dropZone.dispatchEvent(drop);
      },
      {
        selector: dropZoneSelector,
        fileName: config.fileName,
        mimeType: config.mimeType,
        content: typeof config.content === 'string'
          ? config.content
          : config.content
            ? config.content.toString()
            : this.generateDefaultContent(config.mimeType, config.size),
      }
    );
  }

  /**
   * Generates default content based on MIME type
   * @param mimeType - The MIME type
   * @param size - Optional target size
   * @returns Generated content
   */
  private generateDefaultContent(mimeType: string, size?: number): string {
    const targetSize = size || 1024; // Default 1KB

    if (mimeType.startsWith('image/')) {
      // Create a simple PNG header for image files
      return this.generateImageContent(mimeType, targetSize);
    } else if (mimeType === 'application/pdf') {
      // Create a minimal PDF content
      return this.generatePDFContent(targetSize);
    } else if (mimeType.startsWith('text/')) {
      // Generate text content
      return this.generateTextContent(targetSize);
    } else if (mimeType === 'application/json') {
      // Generate JSON content
      return this.generateJSONContent(targetSize);
    } else {
      // Generate binary content
      return this.generateBinaryContent(targetSize);
    }
  }

  /**
   * Generates mock image content
   * @param mimeType - Image MIME type (unused but kept for consistency)
   * @param size - Target size
   * @returns Image content
   */
  private generateImageContent(_mimeType: string, size: number): string {
    // Simple 1x1 pixel PNG
    const pngHeader = Buffer.from([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
      0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
      0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
      0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
      0x89, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x44, 0x41,
      0x54, 0x08, 0x5b, 0x63, 0xf8, 0x0f, 0x00, 0x00,
      0x01, 0x01, 0x01, 0x00, 0x18, 0xdd, 0x8d, 0xb4,
      0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44,
      0xae, 0x42, 0x60, 0x82,
    ]);

    // Pad to requested size
    const padding = Buffer.alloc(Math.max(0, size - pngHeader.length));
    return Buffer.concat([pngHeader, padding]).toString('base64');
  }

  /**
   * Generates mock PDF content
   * @param size - Target size
   * @returns PDF content
   */
  private generatePDFContent(size: number): string {
    const pdfHeader = `%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
365
%%EOF`;

    // Pad to requested size if needed
    const padding = ' '.repeat(Math.max(0, size - pdfHeader.length));
    return pdfHeader + padding;
  }

  /**
   * Generates text content
   * @param size - Target size
   * @returns Text content
   */
  private generateTextContent(size: number): string {
    const text = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. ';
    let content = '';

    while (content.length < size) {
      content += text;
    }

    return content.substring(0, size);
  }

  /**
   * Generates JSON content
   * @param size - Target size
   * @returns JSON content
   */
  private generateJSONContent(size: number): string {
    const data: {
      test: boolean;
      timestamp: number;
      data: Array<{ id: number; value: string }>;
    } = {
      test: true,
      // eslint-disable-next-line no-restricted-properties -- JSON payload emulates real upload metadata with epoch timestamps.
      timestamp: Date.now(),
      data: [],
    };

    // Add array items to reach target size
    while (JSON.stringify(data).length < size) {
      data.data.push({
        id: Math.random(),
        value: 'test-' + Math.random().toString(36),
      });
    }

    const json = JSON.stringify(data, null, 2);
    return json.substring(0, size);
  }

  /**
   * Generates binary content
   * @param size - Target size
   * @returns Binary content as string
   */
  private generateBinaryContent(size: number): string {
    const buffer = Buffer.alloc(size);
    for (let i = 0; i < size; i++) {
      buffer[i] = Math.floor(Math.random() * 256);
    }
    return buffer.toString('base64');
  }

  /**
   * Waits for file upload to complete
   * @param options - Wait options
   */
  async waitForUploadComplete(options?: {
    timeout?: number;
    successSelector?: string;
  }): Promise<void> {
    const timeout = options?.timeout || 10000;

    if (options?.successSelector) {
      await this.page.waitForSelector(options.successSelector, {
        state: 'visible',
        timeout,
      });
    } else {
      // Default: wait for any success indicator
      await this.page.waitForSelector(
        '[data-upload-status="complete"], .upload-success, [aria-label="Upload complete"]',
        { state: 'visible', timeout }
      );
    }
  }

  /**
   * Gets upload progress
   * @param progressSelector - Selector for progress element
   * @returns Progress percentage
   */
  async getUploadProgress(progressSelector?: string): Promise<number> {
    const selector = progressSelector || '[role="progressbar"], .upload-progress, [data-upload-progress]';
    const element = this.page.locator(selector).first();

    if (await element.isVisible()) {
      // Try various methods to get progress
      const ariaValue = await element.getAttribute('aria-valuenow');
      if (ariaValue) {
        return parseFloat(ariaValue);
      }

      const dataProgress = await element.getAttribute('data-progress');
      if (dataProgress) {
        return parseFloat(dataProgress);
      }

      const text = await element.textContent();
      if (text) {
        const match = text.match(/(\d+)%/);
        if (match) {
          return parseFloat(match[1]);
        }
      }
    }

    return 0;
  }

  /**
   * Creates common test files
   * @returns Object with file configurations
   */
  static createTestFiles(): {
    image: FileUploadConfig;
    pdf: FileUploadConfig;
    text: FileUploadConfig;
    json: FileUploadConfig;
  } {
    return {
      image: {
        fileName: 'test-image.png',
        mimeType: 'image/png',
        size: 1024,
      },
      pdf: {
        fileName: 'test-document.pdf',
        mimeType: 'application/pdf',
        size: 2048,
      },
      text: {
        fileName: 'test-file.txt',
        mimeType: 'text/plain',
        content: 'This is a test file content.',
      },
      json: {
        fileName: 'test-data.json',
        mimeType: 'application/json',
        content: JSON.stringify({ test: true, data: 'sample' }),
      },
    };
  }
}

/**
 * Creates a file upload helper for a page
 * @param page - The Playwright page
 * @returns FileUploadHelper instance
 */
export function createFileUploadHelper(page: Page): FileUploadHelper {
  return new FileUploadHelper(page);
}
