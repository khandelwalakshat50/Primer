export type HandFont = {
  id: string;
  name: string;
  family: string;
  size: string;
};

export const HAND_FONTS: HandFont[] = [
  {
    id: "playwrite",
    name: "Playwrite Modern",
    family: '"Playwrite US Modern", cursive',
    size: "1.05rem",
  },
  {
    id: "kalam",
    name: "Kalam",
    family: "Kalam, cursive",
    size: "1.08rem",
  },
  {
    id: "dancing",
    name: "Dancing Script",
    family: '"Dancing Script", cursive',
    size: "1.18rem",
  },
  {
    id: "cormorant",
    name: "Cormorant Garamond",
    family: '"Cormorant Garamond", serif',
    size: "1.08rem",
  },
  {
    id: "playfair",
    name: "Playfair Display",
    family: '"Playfair Display", serif',
    size: "1.02rem",
  },
  {
    id: "bodoni",
    name: "Bodoni Moda",
    family: '"Bodoni Moda", serif',
    size: "1.02rem",
  },
  {
    id: "eb-garamond",
    name: "EB Garamond",
    family: '"EB Garamond", serif',
    size: "1.1rem",
  },
  {
    id: "cinzel",
    name: "Cinzel",
    family: "Cinzel, serif",
    size: "0.95rem",
  },
  {
    id: "great-vibes",
    name: "Great Vibes",
    family: '"Great Vibes", cursive',
    size: "1.38rem",
  },
  {
    id: "allura",
    name: "Allura",
    family: "Allura, cursive",
    size: "1.42rem",
  },
  {
    id: "pinyon",
    name: "Pinyon Script",
    family: '"Pinyon Script", cursive',
    size: "1.28rem",
  },
  {
    id: "parisienne",
    name: "Parisienne",
    family: "Parisienne, cursive",
    size: "1.24rem",
  },
  {
    id: "marcellus",
    name: "Marcellus",
    family: "Marcellus, serif",
    size: "1.05rem",
  },
  {
    id: "instrument",
    name: "Instrument Serif",
    family: '"Instrument Serif", serif',
    size: "1.1rem",
  },
];

export const DEFAULT_FONT_ID = "kalam";

export function fontById(id: string): HandFont {
  return HAND_FONTS.find((font) => font.id === id) ?? HAND_FONTS[1];
}
