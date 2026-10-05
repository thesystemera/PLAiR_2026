import { SceneRenderer } from './sceneRenderer'

let scene = null

self.onmessage = ({ data }) => {
  if (data.type === 'init') {
    scene = new SceneRenderer({
      canvas: data.canvas,
      captureResolution: data.captureResolution,
      glassBlurFactor: data.glassBlurFactor,
      emit: message => self.postMessage(message),
      relayWork: true,
    })
    return
  }
  scene?.handle(data)
}
