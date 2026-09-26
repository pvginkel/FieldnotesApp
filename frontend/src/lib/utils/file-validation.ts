const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
const ALLOWED_FILE_TYPES = {
  images: ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'],
  documents: ['application/pdf'],
} as const;

const ALLOWED_EXTENSIONS = {
  images: ['.jpg', '.jpeg', '.png', '.gif', '.webp'],
  documents: ['.pdf'],
} as const;

export interface ValidationResult {
  isValid: boolean;
  error?: string;
}

export function validateFile(file: File): ValidationResult {
  // Check file size
  if (file.size > MAX_FILE_SIZE) {
    return {
      isValid: false,
      error: `File size must be less than ${MAX_FILE_SIZE / 1024 / 1024}MB`
    };
  }

  // Check MIME type
  const allAllowedTypes = [...ALLOWED_FILE_TYPES.images, ...ALLOWED_FILE_TYPES.documents];
  if (!allAllowedTypes.includes(file.type as any)) {
    return {
      isValid: false,
      error: 'File type not supported. Please use JPG, PNG, GIF, WebP, or PDF files.'
    };
  }

  // Check file extension as backup
  const fileName = file.name.toLowerCase();
  const allAllowedExtensions = [...ALLOWED_EXTENSIONS.images, ...ALLOWED_EXTENSIONS.documents];
  const hasValidExtension = allAllowedExtensions.some(ext => fileName.endsWith(ext));
  
  if (!hasValidExtension) {
    return {
      isValid: false,
      error: 'File extension not supported. Please use JPG, PNG, GIF, WebP, or PDF files.'
    };
  }

  return { isValid: true };
}

export function getFileType(file: File): 'image' | 'document' | 'unknown' {
  if (ALLOWED_FILE_TYPES.images.includes(file.type as any)) {
    return 'image';
  }
  if (ALLOWED_FILE_TYPES.documents.includes(file.type as any)) {
    return 'document';
  }
  return 'unknown';
}

export function isImageFile(file: File): boolean {
  return getFileType(file) === 'image';
}

export function isPdfFile(file: File): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
}

export { MAX_FILE_SIZE, ALLOWED_FILE_TYPES, ALLOWED_EXTENSIONS };