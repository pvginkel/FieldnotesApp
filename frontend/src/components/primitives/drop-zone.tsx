import { useCallback, useState } from 'react';
import { validateFile } from '@/lib/utils/file-validation';
import { UploadIcon } from '@/components/icons/UploadIcon';

interface DropZoneProps {
  onFilesSelected: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  className?: string;
  children?: React.ReactNode;
}

export function DropZone({ 
  onFilesSelected, 
  accept = 'image/*,application/pdf',
  multiple = false,
  disabled = false,
  className = '',
  children
}: DropZoneProps) {
  const [isDragActive, setIsDragActive] = useState(false);
  const [dragError, setDragError] = useState<string | null>(null);

  const validateFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles: File[] = [];
    const errors: string[] = [];

    fileArray.forEach(file => {
      const validation = validateFile(file);
      if (validation.isValid) {
        validFiles.push(file);
      } else {
        errors.push(`${file.name}: ${validation.error}`);
      }
    });

    if (errors.length > 0) {
      setDragError(errors[0]); // Show first error
      setTimeout(() => setDragError(null), 5000);
    }

    return validFiles;
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(false);
    
    if (disabled) return;

    const files = e.dataTransfer.files;
    if (files.length === 0) return;

    const validFiles = validateFiles(files);
    const filesToProcess = multiple ? validFiles : validFiles.slice(0, 1);
    
    if (filesToProcess.length > 0) {
      onFilesSelected(filesToProcess);
    }
  }, [disabled, multiple, onFilesSelected, validateFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!disabled) {
      setIsDragActive(true);
    }
  }, [disabled]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    // Only set drag inactive if we're leaving the drop zone itself, not a child element
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragActive(false);
    }
  }, []);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const validFiles = validateFiles(files);
    const filesToProcess = multiple ? validFiles : validFiles.slice(0, 1);
    
    if (filesToProcess.length > 0) {
      onFilesSelected(filesToProcess);
    }

    // Reset input so same file can be selected again
    e.target.value = '';
  }, [multiple, onFilesSelected, validateFiles]);

  const baseClasses = 'relative border-2 border-dashed rounded-lg transition-colors';
  const stateClasses = disabled 
    ? 'border-muted bg-muted/10 cursor-not-allowed' 
    : isDragActive 
      ? 'border-primary bg-primary/10' 
      : 'border-muted-foreground/25 hover:border-muted-foreground/50';
  
  const errorClasses = dragError ? 'border-red-500 bg-red-50' : '';

  return (
    <div
      className={`${baseClasses} ${stateClasses} ${errorClasses} ${className}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
    >
      <input
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={handleFileInput}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
      />
      
      {children || (
        <div className="flex flex-col items-center justify-center p-6 text-center">
          <div className="mb-2">
            <UploadIcon 
              className={`w-8 h-8 ${isDragActive ? 'text-primary' : 'text-muted-foreground'}`}
            />
          </div>
          <p className={`text-sm ${isDragActive ? 'text-primary' : 'text-muted-foreground'}`}>
            {isDragActive 
              ? 'Drop files here...' 
              : 'Drag and drop files here, or click to select'
            }
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Supports images (JPG, PNG, GIF, WebP) and PDF files
          </p>
        </div>
      )}

      {dragError && (
        <div className="absolute top-2 right-2 bg-red-500 text-white text-xs px-2 py-1 rounded">
          {dragError}
        </div>
      )}
    </div>
  );
}