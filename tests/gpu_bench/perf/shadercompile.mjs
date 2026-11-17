// node shadercompile.mjs - compiles and links every cover-art program from the working copy of
// client/src/lib/depthArtShader.js on the phone's own GPU, in a throwaway WebGL2 context that nothing draws to, and
// prints each program's compile/link log and its active uniforms. Nothing on screen changes.
import { page } from './cdp.mjs'

const sources = []
class FakeWebGL2 {}
globalThis.WebGL2RenderingContext = process.env.FALLBACK ? class Other {} : FakeWebGL2
const gl = new Proxy(new FakeWebGL2(), {
  get(target, name) {
    if (name === 'shaderSource') return (shader, source) => { shader.source = source }
    if (name === 'createShader') return type => ({ type })
    if (name === 'attachShader') return (program, shader) => { (program.shaders ||= []).push(shader) }
    if (name === 'createProgram') return () => { const program = {}; sources.push(program); return program }
    if (name === 'getShaderParameter' || name === 'getProgramParameter') return () => true
    if (name === 'getExtension') return () => ({})
    if (name === 'checkFramebufferStatus') return () => 1
    if (typeof name === 'string' && /^[A-Z_0-9]+$/.test(name)) return 1
    return () => ({})
  },
})
const { createRequire } = await import('node:module')
const { pathToFileURL } = await import('node:url')
const esbuild = createRequire(new URL('../../../client/package.json', import.meta.url))('esbuild')
const bundle = new URL('./.shadercompile.bundle.mjs', import.meta.url)
await esbuild.build({ entryPoints: [new URL('../../../client/src/lib/depthArtShader.js', import.meta.url).pathname.replace(/^\/(\w:)/, '$1')], bundle: true, format: 'esm', define: { 'import.meta.env.DEV': 'false' }, outfile: bundle.pathname.replace(/^\/(\w:)/, '$1'), logLevel: 'error' })
const { createDepthArtPrograms } = await import(pathToFileURL(bundle.pathname.replace(/^\/(\w:)/, '$1')).href)
;(await import('node:fs')).rmSync(bundle)
createDepthArtPrograms(gl, { packedNormals: true, mediumpShading: true })
const programs = sources.map(program => program.shaders.map(shader => ({ type: shader.type, source: shader.source })))

const p = await page()
const result = await p.evaluate(`(() => {
  const canvas = document.createElement('canvas')
  const gl = canvas.getContext('webgl2')
  const programs = ${JSON.stringify(programs)}
  const out = programs.map((shaders, index) => {
    const program = gl.createProgram()
    const logs = []
    shaders.forEach((s, i) => {
      const shader = gl.createShader(i === 0 ? gl.VERTEX_SHADER : gl.FRAGMENT_SHADER)
      gl.shaderSource(shader, s.source)
      gl.compileShader(shader)
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) logs.push((i ? 'fragment: ' : 'vertex: ') + gl.getShaderInfoLog(shader))
      gl.attachShader(program, shader)
    })
    gl.bindAttribLocation(program, 0, 'a_position')
    gl.bindAttribLocation(program, 1, 'a_texCoord')
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) logs.push('link: ' + gl.getProgramInfoLog(program))
    const uniforms = []
    const count = logs.length ? 0 : gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS)
    for (let u = 0; u < count; u++) uniforms.push(gl.getActiveUniform(program, u).name)
    const head = shaders[1].source.split(String.fromCharCode(10)).find(line => /out vec4|gl_FragColor|layout/.test(line)) || ''
    return { index, ok: !logs.length, logs, uniforms, head: head.trim().slice(0, 60) }
  })
  gl.getExtension('WEBGL_lose_context')?.loseContext()
  return JSON.stringify(out)
})()`)
for (const row of JSON.parse(result)) console.log(row.index, row.ok ? 'ok ' : 'FAIL', row.head, row.logs.join(' | ').slice(0, 600), row.ok ? '\n   ' + row.uniforms.join(' ') : '')
p.close()
process.exit(0)
