export default {
  id: 'cover',
  framing: false,
  glsl: `
  vec4 lookImage() {
    return coverImage();
  }
`,
  uniforms: () => ({}),
  signature: [],
  variant: () => ({}),
  frame: () => {},
}
