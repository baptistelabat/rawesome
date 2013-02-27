{-# OPTIONS_GHC -Wall #-}

module ReadMaybe ( readMaybe ) where

readMaybe :: Read a => String -> Maybe a
readMaybe s = case reads s of
    [(x, "")] -> Just x
    _         -> Nothing
