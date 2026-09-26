import { useState, useCallback } from 'react';
import { appendThumbnailParam, generateSrcSetFromUrl, getSizesAttribute, THUMBNAIL_SIZES } from '@/lib/utils/thumbnail-urls';
import { ImagePlaceholderIcon } from '@/components/icons/ImagePlaceholderIcon';

interface ThumbnailProps {
  previewUrl: string | null;
  alt: string;
  size?: 'small' | 'medium' | 'large';
  className?: string;
  onClick?: () => void;
  fallbackIcon?: React.ReactNode;
}

export function Thumbnail({
  previewUrl,
  alt,
  size = 'medium',
  className = '',
  onClick,
  fallbackIcon
}: ThumbnailProps) {
  const [isLoading, setIsLoading] = useState(true);

  const handleImageLoad = useCallback(() => {
    setIsLoading(false);
  }, []);

  const handleImageError = useCallback(() => {
    setIsLoading(false);
  }, []);

  const baseClasses = 'rounded-lg bg-muted flex items-center justify-center overflow-hidden';
  const sizeClasses = {
    small: 'w-16 h-16',
    medium: 'w-24 h-24',
    large: 'w-32 h-32'
  };

  const clickableClasses = onClick ? 'cursor-pointer hover:opacity-80 transition-opacity' : '';
  const combinedClasses = `${baseClasses} ${sizeClasses[size]} ${clickableClasses} ${className}`;

  // Show fallback if no preview URL
  if (!previewUrl) {
    return (
      <div className={combinedClasses} onClick={onClick}>
        {fallbackIcon || <ImagePlaceholderIcon />}
      </div>
    );
  }

  const sizeValue = THUMBNAIL_SIZES[size];
  const thumbnailUrl = appendThumbnailParam(previewUrl, sizeValue);
  const srcSet = generateSrcSetFromUrl(previewUrl);

  return (
    <div className={combinedClasses} onClick={onClick}>
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-primary border-t-transparent" />
        </div>
      )}
      <img
        src={thumbnailUrl ?? undefined}
        srcSet={srcSet}
        sizes={getSizesAttribute()}
        alt={alt}
        className={`w-full h-full object-cover ${isLoading ? 'opacity-0' : 'opacity-100'} transition-opacity`}
        onLoad={handleImageLoad}
        onError={handleImageError}
        loading="lazy"
      />
    </div>
  );
}
