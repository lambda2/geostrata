<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0-Prizren" styleCategories="Symbology">
  <pipe>
    <rasterrenderer type="singlebandpseudocolor" band="1" opacity="0.85" alphaBand="-1" classificationMin="0" classificationMax="3000">
      <rastershader>
        <colorrampshader colorRampType="INTERPOLATED" classificationMode="1" clip="0">
          <item alpha="255" value="0" label="0 m" color="#2b83ba"/>
          <item alpha="255" value="300" label="300 m" color="#abdda4"/>
          <item alpha="255" value="1000" label="1000 m" color="#ffffbf"/>
          <item alpha="255" value="2000" label="2000 m" color="#fdae61"/>
          <item alpha="255" value="3000" label="3000 m" color="#d7191c"/>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
  </pipe>
</qgis>
