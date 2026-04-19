import { extendTheme, type ThemeConfig } from '@chakra-ui/react';

const config: ThemeConfig = {
  initialColorMode: 'light',
  useSystemColorMode: false,
};

const theme = extendTheme({
  config,
  fonts: {
    heading: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    body: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    mono: '"SF Mono", "Fira Code", "Cascadia Code", monospace',
  },
  colors: {
    brand: {
      50: '#f0f4ff',
      100: '#dbe4ff',
      200: '#bac8ff',
      300: '#91a7ff',
      400: '#748ffc',
      500: '#4c6ef5',
      600: '#3b5bdb',
      700: '#364fc7',
      800: '#2b3ea0',
      900: '#1e2d7a',
    },
    surface: {
      primary: '#ffffff',
      secondary: '#f8f9fa',
      tertiary: '#f1f3f5',
    },
    border: {
      subtle: '#f1f3f5',
      default: '#e9ecef',
      strong: '#dee2e6',
    },
    text: {
      primary: '#212529',
      secondary: '#495057',
      tertiary: '#868e96',
      quaternary: '#adb5bd',
    },
    status: {
      idle: '#adb5bd',
      running: '#4c6ef5',
      completed: '#51cf66',
      failed: '#ff6b6b',
    },
    category: {
      entities: { bg: '#eef0ff', text: '#3b3f8c', dot: '#6366f1' },
      relationships: { bg: '#fff0f6', text: '#9c3060', dot: '#e64980' },
      patterns: { bg: '#eefbf0', text: '#2b7a3c', dot: '#40c057' },
      analysis: { bg: '#fff4e6', text: '#b45309', dot: '#fd7e14' },
      sources: { bg: '#e7f5ff', text: '#1971c2', dot: '#339af0' },
      general: { bg: '#f1f3f5', text: '#495057', dot: '#868e96' },
    },
  },
  shadows: {
    card: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)',
    elevated: '0 4px 12px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)',
    float: '0 8px 30px rgba(0,0,0,0.08)',
  },
  radii: {
    card: '12px',
    button: '8px',
    badge: '6px',
    input: '8px',
  },
  fontSizes: {
    micro: '11px',
    caption: '12px',
    body: '14px',
    subtitle: '16px',
    title: '20px',
    display: '24px',
  },
  lineHeights: {
    tight: '1.3',
    normal: '1.5',
    relaxed: '1.75',
  },
  components: {
    Button: {
      defaultProps: {
        size: 'sm',
      },
      baseStyle: {
        fontWeight: '500',
        borderRadius: '8px',
      },
      variants: {
        solid: {
          bg: 'brand.500',
          color: 'white',
          _hover: { bg: 'brand.600' },
          _active: { bg: 'brand.700' },
        },
        ghost: {
          color: 'text.secondary',
          _hover: { bg: 'surface.tertiary', color: 'text.primary' },
        },
        outline: {
          borderColor: 'border.default',
          color: 'text.secondary',
          _hover: { bg: 'surface.secondary', borderColor: 'border.strong' },
        },
      },
    },
    Badge: {
      baseStyle: {
        fontWeight: '500',
        borderRadius: '6px',
        textTransform: 'none',
      },
    },
    Input: {
      defaultProps: { size: 'sm' },
      variants: {
        outline: {
          field: {
            borderRadius: '8px',
            borderColor: 'border.default',
            _hover: { borderColor: 'border.strong' },
            _focus: { borderColor: 'brand.400', boxShadow: '0 0 0 1px var(--chakra-colors-brand-400)' },
          },
        },
        filled: {
          field: {
            bg: 'surface.secondary',
            borderRadius: '8px',
            border: 'none',
            _hover: { bg: 'surface.tertiary' },
            _focus: { bg: 'surface.secondary', borderColor: 'brand.400', border: '1px solid' },
          },
        },
      },
    },
    Modal: {
      baseStyle: {
        dialog: {
          borderRadius: '16px',
        },
        overlay: {
          bg: 'blackAlpha.300',
          backdropFilter: 'blur(4px)',
        },
      },
    },
    Tooltip: {
      baseStyle: {
        borderRadius: '6px',
        fontSize: '12px',
        px: 3,
        py: 1.5,
      },
    },
  },
});

export default theme;
