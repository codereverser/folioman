import { definePreset } from '@primevue/themes'
import Aura from '@primevue/themes/aura'

/**
 * Folioman theme preset.
 *
 * Brand accent is teal; the canvas is slate. Green/red are intentionally NOT
 * mapped here — they belong to gains/losses only and live as app tokens in
 * tokens.css, so PrimeVue components never reach for them as decoration.
 *
 * Contrast notes (WCAG AA on the canvas):
 *  - light fills/links use teal-700 (#0F766E) so white-on-teal clears 4.5:1
 *  - dark accents use teal-400 (#2DD4BF) with dark ink on teal fills
 */
export const FoliomanPreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{teal.50}',
      100: '{teal.100}',
      200: '{teal.200}',
      300: '{teal.300}',
      400: '{teal.400}',
      500: '{teal.500}',
      600: '{teal.600}',
      700: '{teal.700}',
      800: '{teal.800}',
      900: '{teal.900}',
      950: '{teal.950}',
    },
    focusRing: {
      width: '2px',
      style: 'solid',
      color: '{primary.color}',
      offset: '2px',
    },
    colorScheme: {
      light: {
        primary: {
          color: '{teal.700}',
          contrastColor: '#ffffff',
          hoverColor: '{teal.800}',
          activeColor: '{teal.900}',
        },
        highlight: {
          background: '{teal.50}',
          focusBackground: '{teal.100}',
          color: '{teal.800}',
          focusColor: '{teal.900}',
        },
        surface: {
          0: '#ffffff',
          50: '#F6F8FB',
          100: '#F1F5F9',
          200: '#E2E8F0',
          300: '#CBD5E1',
          400: '#94A3B8',
          500: '#64748B',
          600: '#475569',
          700: '#334155',
          800: '#1E293B',
          900: '#0F172A',
          950: '#0B1120',
        },
        content: {
          background: '#ffffff',
          hoverBackground: '#F1F5F9',
          borderColor: '#E2E8F0',
          color: '#0F172A',
          hoverColor: '#0F172A',
        },
      },
      dark: {
        primary: {
          color: '{teal.400}',
          contrastColor: '#04231F',
          hoverColor: '{teal.300}',
          activeColor: '{teal.200}',
        },
        highlight: {
          background: 'color-mix(in srgb, {teal.500}, transparent 86%)',
          focusBackground: 'color-mix(in srgb, {teal.500}, transparent 78%)',
          color: '{teal.300}',
          focusColor: '{teal.200}',
        },
        surface: {
          0: '#E8EDF4',
          50: '#CBD5E1',
          100: '#94A3B8',
          200: '#64748B',
          300: '#475569',
          400: '#334155',
          500: '#2A3A55',
          600: '#1F2C46',
          700: '#1B2740',
          800: '#131C2E',
          900: '#0B1120',
          950: '#060B16',
        },
        content: {
          background: '#131C2E',
          hoverBackground: '#1B2740',
          borderColor: '#2A3A55',
          color: '#E8EDF4',
          hoverColor: '#E8EDF4',
        },
      },
    },
  },
  components: {
    // Aura's secondary buttons (outlined + text) read `color: {surface.500}` with
    // no dark override. In our dark ramp surface.500 (#2A3A55) is nearly the card
    // colour, so the label vanishes; its hover/active also point at *light*
    // surfaces (50/100), which flash on a dark canvas. Re-point the dark variants
    // at a readable slate + dark hover. Light mode is fine and left untouched.
    button: {
      colorScheme: {
        dark: {
          outlined: {
            secondary: {
              color: '{surface.50}',
              borderColor: '{surface.200}',
              hoverBackground: '{surface.700}',
              activeBackground: '{surface.600}',
            },
          },
          text: {
            secondary: {
              color: '{surface.50}',
              hoverBackground: '{surface.700}',
              activeBackground: '{surface.600}',
            },
          },
        },
      },
    },
  },
})
