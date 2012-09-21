{-# OPTIONS_GHC -Wall #-}

module Draw ( drawAc
            , drawTrails
            ) where

import SpatialMath
import Vis

drawAc :: Xyz Double -> Quat Double -> (VisObject Double, [Xyz Double])
drawAc pos quat = (VisObjects $ wing ++ [htail,vtail,body,axes], vtip:wingtips)
  where
    axes = VisAxes (0.5, 15) pos quat
    spanW = 0.96
    arW = 9
    
    spanHRatio = 0.4
    arH = 4

    spanVRatio = 0.15
    arV = 2
    
    lengthToSpan = 0.7
    
    len = spanW*lengthToSpan
    width = 0.05
    
    wingPosOverLen = 0.2
    
    deltaWingTail = len*(1-wingPosOverLen)
    
    dcm = dcmOfQuat quat
    rotateTranslate = (pos +) . (rotVecByDcmB2A dcm)

    spanV = spanW*spanVRatio
    
    wingtips = map rotateTranslate [Xyz 0 (-spanW/2) 0, Xyz 0 (spanW/2) 0]
    vtip = rotateTranslate $ Xyz (-deltaWingTail) 0 (-spanV)
    
    wing = [ VisQuad
             (rotateTranslate $ Xyz ( chordW/2) ( spanW/2) 0)
             (rotateTranslate $ Xyz ( chordW/2) (-spanW/2) 0)
             (rotateTranslate $ Xyz (-chordW/2) (-spanW/2) 0)
             (rotateTranslate $ Xyz (-chordW/2) ( spanW/2) 0)
             blue
           , VisQuad
             (rotateTranslate $ Xyz (-chordW/2) ( spanW/2) (-0.01))
             (rotateTranslate $ Xyz (-chordW/2) (-spanW/2) (-0.01))
             (rotateTranslate $ Xyz ( chordW/2) (-spanW/2) (-0.01))
             (rotateTranslate $ Xyz ( chordW/2) ( spanW/2) (-0.01))
             yellow
           ]
      where
        chordW = spanW/arW
    
        
    htail = VisQuad
            (rotateTranslate $ Xyz (-deltaWingTail + chordH/2) ( spanH/2) 0)
            (rotateTranslate $ Xyz (-deltaWingTail + chordH/2) (-spanH/2) 0)
            (rotateTranslate $ Xyz (-deltaWingTail - chordH/2) (-spanH/2) 0)
            (rotateTranslate $ Xyz (-deltaWingTail - chordH/2) ( spanH/2) 0)
            blue
      where
        spanH = spanW*spanHRatio
        chordH = spanH/arH
        
    vtail = VisQuad
            (rotateTranslate $ Xyz (-deltaWingTail + chordV/2) 0 (-spanV))
            (rotateTranslate $ Xyz (-deltaWingTail + chordV/2) 0 (     0))
            (rotateTranslate $ Xyz (-deltaWingTail - chordV/2) 0 (     0))
            (rotateTranslate $ Xyz (-deltaWingTail - chordV/2) 0 (-spanV))
            yellow
      where
        chordV = spanV/arV
            
    body = VisEllipsoid (len/2, width/2, width/2)
           (rotateTranslate $ Xyz (len/2-deltaWingTail) 0 0)
           quat
           Solid
           blue

drawTrails :: [[Xyz a]] -> VisObject a
drawTrails xyzs = VisObjects $ zipWith drawTrail xyzs $ cycle [makeColor 0 0 1, makeColor 0 1 0, makeColor 1 0 0]

drawTrail :: [Xyz a] -> (Float -> Color) -> VisObject a
drawTrail trail mkCol = VisLine' $ zip trail (map mkCol (linspace 1 0 (length trail)))
  where
    linspace :: Fractional a => a -> a -> Int -> [a]
    linspace x0 xf n = map (\k -> x0 + (xf - x0) * (fromIntegral k) / (fromIntegral n-1)) [0..(n-1)]
